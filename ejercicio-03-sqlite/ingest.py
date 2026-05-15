"""Ingesta del Parquet de 1M filas a una base SQLite.

Reglas implementadas (segun el PDF del modulo):

* CLI con --chunk-size.
* Transacciones explicitas: un BEGIN/COMMIT por chunk, no por fila.
* Probar con y sin WAL via --wal / --no-wal.
* Reportar tiempo de ingesta en ambos casos.
* La ingesta de 1M filas debe completarse en menos de 3 minutos.
* Crea SOLO la tabla (schema.sql).  Los indices los aplica el benchmark
  para poder medir CON y SIN ellos.

Uso:
    python ingest.py --parquet ../data/benchmark_1m/transactions.snappy.parquet \\
                     --db ./db/transactions_wal.db --chunk-size 50000 --wal
    python ingest.py --parquet ../data/benchmark_1m/transactions.snappy.parquet \\
                     --db ./db/transactions_nowal.db --chunk-size 50000 --no-wal
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.sql"
DEFAULT_PARQUET = HERE.parent / "data" / "benchmark_1m" / "transactions.snappy.parquet"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ingesta del Parquet a SQLite.")
    p.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET,
                   help=f"Parquet de origen (default: {DEFAULT_PARQUET}).")
    p.add_argument("--db", type=Path, required=True,
                   help="Path del archivo .db a crear.")
    p.add_argument("--chunk-size", type=int, default=50_000,
                   help="Filas por transaccion (default 50 000).")
    wal = p.add_mutually_exclusive_group()
    wal.add_argument("--wal", dest="wal", action="store_true",
                     help="Activa journal_mode=WAL (default).")
    wal.add_argument("--no-wal", dest="wal", action="store_false",
                     help="Usa journal_mode=DELETE (rollback journal clasico).")
    p.set_defaults(wal=True)
    return p.parse_args()


def init_db(db_path: Path, wal: bool) -> sqlite3.Connection:
    """Crea la base desde cero a partir de schema.sql + pragmas optimizados."""
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    # PRAGMAs antes del schema para que apliquen a la creacion misma.
    if wal:
        cur.execute("PRAGMA journal_mode = WAL")
        # WAL permite synchronous=NORMAL sin riesgo de corrupcion en crash de aplicacion
        # (solo se puede perder la ultima transaccion no-fsynced).
        cur.execute("PRAGMA synchronous = NORMAL")
    else:
        cur.execute("PRAGMA journal_mode = DELETE")
        cur.execute("PRAGMA synchronous = FULL")

    # cache_size negativo = KB (no paginas).  -200000 = 200 MB.  Acomoda toda la tabla en RAM.
    cur.execute("PRAGMA cache_size = -200000")
    # temp_store en memoria para sorts intermedios (no aplica aqui, pero costo cero).
    cur.execute("PRAGMA temp_store = MEMORY")

    schema_sql = SCHEMA_PATH.read_text()
    cur.executescript(schema_sql)
    con.commit()
    return con


def ingest(con: sqlite3.Connection, parquet_path: Path, chunk_size: int) -> dict[str, float]:
    """Lee el Parquet por chunks y los inserta con executemany dentro de transacciones."""
    pf = pq.ParquetFile(parquet_path)
    cur = con.cursor()

    insert_sql = (
        "INSERT INTO transactions "
        "(transaction_id, timestamp, user_id, merchant_id, amount, "
        " category, country_code, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )

    total_rows = 0
    chunk_times: list[float] = []
    t_start = time.perf_counter()

    for batch in pf.iter_batches(batch_size=chunk_size):
        df = batch.to_pandas()
        # Convertir Timestamps a ISO 8601 (texto) — orden lexicografico = orden cronologico.
        df["timestamp"] = df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
        rows = list(df[[
            "transaction_id", "timestamp", "user_id", "merchant_id",
            "amount", "category", "country_code", "status",
        ]].itertuples(index=False, name=None))

        t_chunk = time.perf_counter()
        cur.execute("BEGIN")
        cur.executemany(insert_sql, rows)
        con.commit()
        chunk_times.append(time.perf_counter() - t_chunk)
        total_rows += len(rows)

    t_total = time.perf_counter() - t_start
    return {
        "total_rows": total_rows,
        "total_seconds": t_total,
        "chunk_count": len(chunk_times),
        "chunk_seconds_mean": sum(chunk_times) / max(len(chunk_times), 1),
        "chunk_seconds_min": min(chunk_times) if chunk_times else 0.0,
        "chunk_seconds_max": max(chunk_times) if chunk_times else 0.0,
    }


def db_file_bytes(db_path: Path) -> int:
    """Tamano del .db (incluye -wal y -shm si existen)."""
    total = db_path.stat().st_size
    for suffix in ("-wal", "-shm", "-journal"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            total += side.stat().st_size
    return total


def main() -> None:
    args = parse_args()
    if not args.parquet.exists():
        raise FileNotFoundError(f"Parquet no encontrado: {args.parquet}")

    print(f"Parquet:    {args.parquet}")
    print(f"DB:         {args.db}")
    print(f"Chunk size: {args.chunk_size:,}")
    print(f"Journal:    {'WAL' if args.wal else 'DELETE (rollback journal)'}")
    print()

    con = init_db(args.db, args.wal)
    metrics = ingest(con, args.parquet, args.chunk_size)
    # Truncar el WAL al cerrar para que el .db sea autosuficiente al medir tamano.
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()

    metrics.update({
        "parquet": str(args.parquet),
        "db": str(args.db),
        "chunk_size": args.chunk_size,
        "wal": args.wal,
        "db_bytes_after_ingest": db_file_bytes(args.db),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    })

    print(f"Ingesta:    {metrics['total_rows']:,} filas en {metrics['total_seconds']:.2f}s "
          f"({metrics['total_rows'] / metrics['total_seconds']:,.0f} filas/s)")
    print(f"Chunks:     {metrics['chunk_count']}  "
          f"(mean={metrics['chunk_seconds_mean']*1000:.0f}ms, "
          f"min={metrics['chunk_seconds_min']*1000:.0f}ms, "
          f"max={metrics['chunk_seconds_max']*1000:.0f}ms)")
    print(f"DB size:    {metrics['db_bytes_after_ingest'] / 1e6:.2f} MB")

    # Append-mode: cada corrida agrega una entrada al JSON de ingest para tener ambas (WAL y no-WAL).
    results_dir = HERE / "results"
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / "ingest.json"
    history: list[dict] = []
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
            history = existing.get("runs", [])
        except json.JSONDecodeError:
            history = []
    history.append(metrics)
    out_path.write_text(json.dumps({"runs": history}, indent=2))
    print(f"Metricas -> {out_path}")


if __name__ == "__main__":
    main()
