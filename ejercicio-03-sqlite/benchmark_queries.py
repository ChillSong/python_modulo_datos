"""Benchmark de los 5 patrones de acceso transaccionales.

Para cada patron P1..P5:
  1. Mide 100 ejecuciones con parametros aleatorios (seed fijo) SIN indices.
  2. Captura EXPLAIN QUERY PLAN para una invocacion representativa.
  3. Crea los indices (solo una vez tras la fase 1) y mide otras 100 ejecuciones.
  4. Captura EXPLAIN QUERY PLAN con indices.
  5. Mide las mismas 100 invocaciones contra DuckDB sobre el Parquet del E1.

Se reporta mean, p50, p95, p99 por escenario.  La validacion de SLA usa p95.

Uso:
    python benchmark_queries.py
    python benchmark_queries.py --reps 100 --db db/transactions_wal.db --seed 42
"""

from __future__ import annotations

import argparse
import gc
import json
import random
import sqlite3
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "db" / "transactions_wal.db"
DEFAULT_PARQUET = HERE.parent / "data" / "benchmark_1m" / "transactions.snappy.parquet"

SLA_MS = {"P1": 10, "P2": 50, "P3": 50, "P4": 50, "P5": 200}
P5_N = 20
P3_WINDOW_DAYS = 7
P4_WINDOW_DAYS = 30


# ---------------------------------------------------------------------------
# Patrones — SQL para SQLite y DuckDB
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pattern:
    pid: str
    description: str
    sqlite_sql: str
    duckdb_sql: str


PATTERNS: list[Pattern] = [
    Pattern(
        "P1",
        "Buscar una transaccion por transaction_id exacto",
        "SELECT * FROM transactions WHERE transaction_id = ?",
        "SELECT * FROM txns WHERE transaction_id = ?",
    ),
    Pattern(
        "P2",
        "Ultimas 20 transacciones de un user_id ordenadas por timestamp DESC",
        "SELECT * FROM transactions WHERE user_id = ? "
        "ORDER BY timestamp DESC LIMIT 20",
        "SELECT * FROM txns WHERE user_id = ? "
        "ORDER BY timestamp DESC LIMIT 20",
    ),
    Pattern(
        "P3",
        "Transacciones de un user_id en un rango de fechas",
        "SELECT * FROM transactions WHERE user_id = ? "
        "AND timestamp >= ? AND timestamp < ?",
        "SELECT * FROM txns WHERE user_id = ? "
        "AND timestamp >= ? AND timestamp < ?",
    ),
    Pattern(
        "P4",
        "Suma de amount de un user_id en el ultimo mes",
        "SELECT SUM(amount) FROM transactions "
        "WHERE user_id = ? AND timestamp >= ?",
        "SELECT SUM(amount) FROM txns "
        "WHERE user_id = ? AND timestamp >= ?",
    ),
    Pattern(
        "P5",
        f"user_id de un country_code con > {P5_N} transacciones",
        "SELECT user_id, COUNT(*) AS n FROM transactions "
        "WHERE country_code = ? GROUP BY user_id HAVING COUNT(*) > ?",
        "SELECT user_id, COUNT(*) AS n FROM txns "
        "WHERE country_code = ? GROUP BY user_id HAVING COUNT(*) > ?",
    ),
]


INDEX_DDL = [
    "CREATE INDEX idx_txns_user_timestamp "
    "ON transactions (user_id, timestamp DESC)",
    "CREATE INDEX idx_txns_country_user "
    "ON transactions (country_code, user_id)",
]


# ---------------------------------------------------------------------------
# Parametros aleatorios para cada patron
# ---------------------------------------------------------------------------

def generate_params(parquet: Path, reps: int, seed: int) -> dict[str, list[tuple]]:
    """Muestrea parametros realistas para los 5 patrones desde el Parquet."""
    df = pd.read_parquet(parquet, columns=[
        "transaction_id", "user_id", "country_code", "timestamp",
    ])
    rng = random.Random(seed)

    txn_ids = df["transaction_id"].sample(n=reps, random_state=seed).tolist()
    user_ids_p2 = df["user_id"].sample(n=reps, random_state=seed + 1).tolist()
    user_ids_p3 = df["user_id"].sample(n=reps, random_state=seed + 2).tolist()
    user_ids_p4 = df["user_id"].sample(n=reps, random_state=seed + 3).tolist()
    countries = df["country_code"].drop_duplicates().tolist()
    p5_countries = [rng.choice(countries) for _ in range(reps)]

    ts_min = df["timestamp"].min()
    ts_max = df["timestamp"].max()
    span_days = (ts_max - ts_min).days

    # P3: ventana aleatoria de P3_WINDOW_DAYS dentro del rango del dataset.
    p3_params: list[tuple] = []
    for u in user_ids_p3:
        offset = rng.randint(0, max(span_days - P3_WINDOW_DAYS, 0))
        start = ts_min + timedelta(days=offset)
        end = start + timedelta(days=P3_WINDOW_DAYS)
        p3_params.append((int(u), start.isoformat(), end.isoformat()))

    # P4: cutoff = ts_max - 30 dias (constante; user_id varia).
    p4_cutoff = (ts_max - timedelta(days=P4_WINDOW_DAYS)).isoformat()
    p4_params = [(int(u), p4_cutoff) for u in user_ids_p4]

    return {
        "P1": [(t,) for t in txn_ids],
        "P2": [(int(u),) for u in user_ids_p2],
        "P3": p3_params,
        "P4": p4_params,
        "P5": [(c, P5_N) for c in p5_countries],
    }


# ---------------------------------------------------------------------------
# Medicion
# ---------------------------------------------------------------------------

def _percentile(values: list[float], pct: float) -> float:
    """Percentil sin numpy.  values en cualquier orden."""
    if not values:
        return 0.0
    sorted_v = sorted(values)
    k = (len(sorted_v) - 1) * pct / 100.0
    f = int(k)
    c = min(f + 1, len(sorted_v) - 1)
    if f == c:
        return sorted_v[f]
    return sorted_v[f] + (sorted_v[c] - sorted_v[f]) * (k - f)


def summarize(times_seconds: list[float]) -> dict[str, float]:
    times_ms = [t * 1000 for t in times_seconds]
    return {
        "reps": len(times_ms),
        "mean_ms": float(statistics.mean(times_ms)),
        "min_ms": float(min(times_ms)),
        "max_ms": float(max(times_ms)),
        "p50_ms": float(_percentile(times_ms, 50)),
        "p95_ms": float(_percentile(times_ms, 95)),
        "p99_ms": float(_percentile(times_ms, 99)),
        "stdev_ms": float(statistics.stdev(times_ms)) if len(times_ms) > 1 else 0.0,
        "total_seconds": sum(times_seconds),
    }


def measure_sqlite(
    con: sqlite3.Connection, sql: str, params_list: list[tuple]
) -> tuple[list[float], int]:
    times: list[float] = []
    total_rows = 0
    cur = con.cursor()
    for params in params_list:
        gc.collect()
        t0 = time.perf_counter()
        cur.execute(sql, params)
        rows = cur.fetchall()
        times.append(time.perf_counter() - t0)
        total_rows += len(rows)
    return times, total_rows


def measure_duckdb(
    con: duckdb.DuckDBPyConnection, sql: str, params_list: list[tuple]
) -> tuple[list[float], int]:
    times: list[float] = []
    total_rows = 0
    for params in params_list:
        gc.collect()
        t0 = time.perf_counter()
        rows = con.execute(sql, list(params)).fetchall()
        times.append(time.perf_counter() - t0)
        total_rows += len(rows)
    return times, total_rows


def explain_sqlite(con: sqlite3.Connection, sql: str, params: tuple) -> str:
    rows = con.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    lines = []
    for r in rows:
        # row shape: (id, parent, notused, detail)
        lines.append(f"  [{r[0]}|{r[1]}] {r[3]}")
    return "\n".join(lines)


def explain_duckdb(con: duckdb.DuckDBPyConnection, sql: str, params: tuple) -> str:
    rows = con.execute("EXPLAIN " + sql, list(params)).fetchall()
    return "\n".join(r[1] for r in rows)


# ---------------------------------------------------------------------------
# Conexiones
# ---------------------------------------------------------------------------

def open_sqlite(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(db)
    con.execute("PRAGMA cache_size = -200000")
    con.execute("PRAGMA temp_store = MEMORY")
    return con


def open_duckdb(parquet: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(f"CREATE OR REPLACE VIEW txns AS SELECT * FROM read_parquet('{parquet}')")
    return con


def drop_indexes(con: sqlite3.Connection) -> None:
    con.execute("DROP INDEX IF EXISTS idx_txns_user_timestamp")
    con.execute("DROP INDEX IF EXISTS idx_txns_country_user")
    con.commit()


def create_indexes(con: sqlite3.Connection) -> None:
    for ddl in INDEX_DDL:
        con.execute(ddl)
    con.execute("ANALYZE")
    con.commit()


def db_size_bytes(db: Path) -> int:
    total = db.stat().st_size
    for suffix in ("-wal", "-shm"):
        side = db.with_name(db.name + suffix)
        if side.exists():
            total += side.stat().st_size
    return total


# ---------------------------------------------------------------------------
# Orquestacion
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark de 5 patrones SQLite + DuckDB.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET)
    p.add_argument("--reps", type=int, default=100)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--output", type=Path, default=HERE / "results")
    return p.parse_args()


def run_phase(
    label: str,
    con_sqlite: sqlite3.Connection,
    params: dict[str, list[tuple]],
) -> dict[str, dict[str, Any]]:
    """Mide los 5 patrones en SQLite con el estado actual de indices."""
    print(f"\n=== SQLite — {label} ===")
    out: dict[str, dict[str, Any]] = {}
    for pat in PATTERNS:
        plist = params[pat.pid]
        explain_text = explain_sqlite(con_sqlite, pat.sqlite_sql, plist[0])
        times, total_rows = measure_sqlite(con_sqlite, pat.sqlite_sql, plist)
        stats = summarize(times)
        sla = SLA_MS[pat.pid]
        ok = stats["p95_ms"] < sla
        flag = "OK " if ok else "FAIL"
        out[pat.pid] = {
            "times_ms": [t * 1000 for t in times],
            "summary": stats,
            "total_rows_returned": total_rows,
            "sla_ms": sla,
            "p95_meets_sla": ok,
            "explain_query_plan": explain_text,
        }
        print(f"  {pat.pid} {flag}  mean={stats['mean_ms']:6.2f}ms  "
              f"p50={stats['p50_ms']:6.2f}ms  p95={stats['p95_ms']:6.2f}ms  "
              f"p99={stats['p99_ms']:6.2f}ms  sla={sla}ms")
    return out


def run_duckdb(
    con_duck: duckdb.DuckDBPyConnection, params: dict[str, list[tuple]]
) -> dict[str, dict[str, Any]]:
    print(f"\n=== DuckDB (read_parquet directo, sin indices) ===")
    out: dict[str, dict[str, Any]] = {}
    for pat in PATTERNS:
        plist = params[pat.pid]
        explain_text = explain_duckdb(con_duck, pat.duckdb_sql, plist[0])
        times, total_rows = measure_duckdb(con_duck, pat.duckdb_sql, plist)
        stats = summarize(times)
        sla = SLA_MS[pat.pid]
        ok = stats["p95_ms"] < sla
        flag = "OK " if ok else "FAIL"
        out[pat.pid] = {
            "times_ms": [t * 1000 for t in times],
            "summary": stats,
            "total_rows_returned": total_rows,
            "sla_ms": sla,
            "p95_meets_sla": ok,
            "explain": explain_text,
        }
        print(f"  {pat.pid} {flag}  mean={stats['mean_ms']:6.2f}ms  "
              f"p50={stats['p50_ms']:6.2f}ms  p95={stats['p95_ms']:6.2f}ms  "
              f"p99={stats['p99_ms']:6.2f}ms  sla={sla}ms")
    return out


def main() -> None:
    args = parse_args()
    if not args.db.exists():
        raise FileNotFoundError(
            f"DB no encontrada: {args.db}.  Corre ingest.py primero."
        )
    if not args.parquet.exists():
        raise FileNotFoundError(f"Parquet no encontrado: {args.parquet}.")

    args.output.mkdir(parents=True, exist_ok=True)

    print(f"DB:       {args.db}")
    print(f"Parquet:  {args.parquet}")
    print(f"Reps:     {args.reps}    Seed: {args.seed}")
    print(f"P5 N:     {P5_N}  (umbral de transacciones por user_id)")

    print("\nGenerando parametros aleatorios desde el Parquet...")
    params = generate_params(args.parquet, args.reps, args.seed)

    # Asegurar estado limpio: sin indices secundarios.
    con_sqlite = open_sqlite(args.db)
    drop_indexes(con_sqlite)
    db_bytes_no_idx = db_size_bytes(args.db)

    no_idx = run_phase("sin indices secundarios", con_sqlite, params)

    print("\nCreando indices secundarios...")
    t0 = time.perf_counter()
    create_indexes(con_sqlite)
    idx_seconds = time.perf_counter() - t0
    db_bytes_with_idx = db_size_bytes(args.db)
    print(f"  Indices creados en {idx_seconds:.2f}s")
    print(f"  DB pre-indices:  {db_bytes_no_idx/1e6:.2f} MB")
    print(f"  DB post-indices: {db_bytes_with_idx/1e6:.2f} MB "
          f"(+{(db_bytes_with_idx - db_bytes_no_idx)/1e6:.2f} MB)")

    with_idx = run_phase("con indices secundarios", con_sqlite, params)

    con_sqlite.close()

    con_duck = open_duckdb(args.parquet)
    duck_results = run_duckdb(con_duck, params)
    con_duck.close()

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "db": str(args.db),
        "parquet": str(args.parquet),
        "reps": args.reps,
        "seed": args.seed,
        "p5_threshold_n": P5_N,
        "p3_window_days": P3_WINDOW_DAYS,
        "p4_window_days": P4_WINDOW_DAYS,
        "index_build_seconds": idx_seconds,
        "db_bytes_no_indexes": db_bytes_no_idx,
        "db_bytes_with_indexes": db_bytes_with_idx,
        "sqlite_no_indexes": no_idx,
        "sqlite_with_indexes": with_idx,
        "duckdb_parquet": duck_results,
    }
    out_json = args.output / "benchmark.json"
    out_json.write_text(json.dumps(payload, indent=2, default=str))
    print(f"\nResultados -> {out_json}")

    # EXPLAIN QUERY PLAN en archivo aparte para que el reporte lo cite.
    out_explain = args.output / "explain_query_plan.txt"
    with out_explain.open("w") as f:
        for pat in PATTERNS:
            f.write(f"=== {pat.pid} — {pat.description} ===\n\n")
            f.write(f"-- SQL (SQLite) --\n{pat.sqlite_sql}\n\n")
            f.write(f"-- SIN indices --\n{no_idx[pat.pid]['explain_query_plan']}\n\n")
            f.write(f"-- CON indices --\n{with_idx[pat.pid]['explain_query_plan']}\n\n")
            f.write(f"-- DuckDB EXPLAIN --\n{duck_results[pat.pid]['explain']}\n\n\n")
    print(f"EXPLAIN    -> {out_explain}")


if __name__ == "__main__":
    main()
