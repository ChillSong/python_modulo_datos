"""Capa de datos dual: DuckDB (analytics) + SQLite (transaccional).

Ambas conexiones se abren UNA vez en el lifespan de FastAPI y viven en
`app.state` — nunca dentro de un endpoint (la spec lo marca como error de
arquitectura y el benchmark lo delataria).

Reparto de responsabilidades (justificado en architecture_decision.md):

* DuckDB, en memoria, con una vista sobre el Parquet de 1M filas del E1.
  Sirve los agregados globales (/analytics/*): escaneos columnares sobre
  toda la tabla, justo donde un motor OLAP gana.
* SQLite, la base del E3 con sus 2 indices.  Sirve los accesos por usuario
  (/users/*) y las escrituras (/transactions/batch): lookups puntuales
  indexados e insert transaccional.

Concurrencia: uvicorn ejecuta los endpoints sync en un threadpool.  Una
`sqlite3.Connection` no es segura para uso concurrente, y una conexion
DuckDB tampoco debe compartirse sin cuidado, asi que cada backend se
protege con su propio Lock.  El benchmark de latencia es secuencial, asi
que la contencion es nula; para concurrencia real se usaria un pool de
conexiones de lectura (WAL lo permite — ver E3).
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path

import duckdb

from .models import TransactionIn

HERE = Path(__file__).resolve().parent
DEFAULT_PARQUET = HERE.parent.parent / "data" / "benchmark_1m" / "transactions.snappy.parquet"
DEFAULT_DB = HERE.parent / "db" / "transactions.db"

_TS_FMT = "%Y-%m-%dT%H:%M:%S.%f"  # mismo formato que setup_db.py


@dataclass
class Backends:
    duck: duckdb.DuckDBPyConnection
    sqlite: sqlite3.Connection
    duck_lock: threading.Lock = field(default_factory=threading.Lock)
    sqlite_lock: threading.Lock = field(default_factory=threading.Lock)

    def healthy(self) -> dict[str, bool]:
        ok = {}
        try:
            with self.duck_lock:
                self.duck.execute("SELECT 1")
            ok["duckdb"] = True
        except Exception:
            ok["duckdb"] = False
        try:
            with self.sqlite_lock:
                self.sqlite.execute("SELECT 1")
            ok["sqlite"] = True
        except Exception:
            ok["sqlite"] = False
        return ok

    def close(self) -> None:
        with self.duck_lock:
            self.duck.close()
        with self.sqlite_lock:
            self.sqlite.close()


def init_backends(parquet: Path | None = None, db_path: Path | None = None) -> Backends:
    parquet = parquet or DEFAULT_PARQUET
    db_path = db_path or DEFAULT_DB
    if not parquet.exists():
        raise FileNotFoundError(f"Parquet no encontrado para analytics: {parquet}")
    if not db_path.exists():
        raise FileNotFoundError(
            f"SQLite no encontrado: {db_path}. Corre primero: "
            "uv run python ejercicio-04-sistema/setup_db.py"
        )

    duck = duckdb.connect(":memory:")
    duck.execute(
        f"CREATE OR REPLACE VIEW txns AS SELECT * FROM read_parquet('{parquet}')"
    )

    sqlite = sqlite3.connect(str(db_path), check_same_thread=False)
    sqlite.row_factory = sqlite3.Row
    sqlite.execute("PRAGMA journal_mode = WAL")
    sqlite.execute("PRAGMA synchronous = NORMAL")
    sqlite.execute("PRAGMA cache_size = -200000")
    return Backends(duck=duck, sqlite=sqlite)


# ---------------------------------------------------------------------------
# Analytics (DuckDB sobre el Parquet)
# ---------------------------------------------------------------------------

def analytics_summary(b: Backends) -> dict:
    with b.duck_lock:
        n, total, avg = b.duck.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0), COALESCE(AVG(amount), 0) FROM txns"
        ).fetchone()
        by_country = b.duck.execute(
            "SELECT country_code, COUNT(*) AS n, SUM(amount) AS total "
            "FROM txns GROUP BY country_code "
            "ORDER BY n DESC, country_code ASC"
        ).fetchall()
        by_category = b.duck.execute(
            "SELECT category, COUNT(*) AS n, SUM(amount) AS total "
            "FROM txns GROUP BY category "
            "ORDER BY n DESC, category ASC"
        ).fetchall()
    return {
        "n_transactions": int(n),
        "total_amount": round(float(total), 2),
        "avg_amount": round(float(avg), 2),
        "by_country": [
            {"country_code": c, "n_transactions": int(cn), "total_amount": round(float(ct), 2)}
            for c, cn, ct in by_country
        ],
        "by_category": [
            {"category": cat, "n_transactions": int(cn), "total_amount": round(float(ct), 2)}
            for cat, cn, ct in by_category
        ],
    }


def top_merchants(b: Backends, limit: int, country: str | None) -> list[dict]:
    sql = (
        "SELECT merchant_id, COUNT(*) AS n, SUM(amount) AS total FROM txns "
    )
    params: list = []
    if country is not None:
        sql += "WHERE country_code = ? "
        params.append(country)
    sql += "GROUP BY merchant_id ORDER BY total DESC, merchant_id ASC LIMIT ?"
    params.append(limit)
    with b.duck_lock:
        rows = b.duck.execute(sql, params).fetchall()
    return [
        {"merchant_id": int(m), "n_transactions": int(n), "total_amount": round(float(t), 2)}
        for m, n, t in rows
    ]


# ---------------------------------------------------------------------------
# Transaccional (SQLite)
# ---------------------------------------------------------------------------

def user_exists(b: Backends, user_id: int) -> bool:
    with b.sqlite_lock:
        row = b.sqlite.execute(
            "SELECT 1 FROM transactions WHERE user_id = ? LIMIT 1", (user_id,)
        ).fetchone()
    return row is not None


def user_transactions(b: Backends, user_id: int, page: int, page_size: int) -> list[dict]:
    offset = (page - 1) * page_size
    with b.sqlite_lock:
        rows = b.sqlite.execute(
            "SELECT transaction_id, timestamp, user_id, merchant_id, amount, "
            "category, country_code, status "
            "FROM transactions WHERE user_id = ? "
            "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (user_id, page_size, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def user_stats(b: Backends, user_id: int) -> dict | None:
    with b.sqlite_lock:
        agg = b.sqlite.execute(
            "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total "
            "FROM transactions WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if agg is None or agg["n"] == 0:
            return None
        cat = b.sqlite.execute(
            "SELECT category, COUNT(*) AS n FROM transactions WHERE user_id = ? "
            "GROUP BY category ORDER BY n DESC, category ASC LIMIT 1",
            (user_id,),
        ).fetchone()
        country = b.sqlite.execute(
            "SELECT country_code, COUNT(*) AS n FROM transactions WHERE user_id = ? "
            "GROUP BY country_code ORDER BY n DESC, country_code ASC LIMIT 1",
            (user_id,),
        ).fetchone()
    return {
        "user_id": user_id,
        "n_transactions": int(agg["n"]),
        "total_amount": round(float(agg["total"]), 2),
        "most_frequent_category": cat["category"],
        "country_code": country["country_code"],
    }


def insert_batch(b: Backends, txns: list[TransactionIn]) -> tuple[int, list[str]]:
    """Deduplica por transaction_id (intra-lote y contra la base) e inserta.

    Devuelve (insertados, ids_duplicados).
    """
    # 1. Deduplicar dentro del lote conservando la primera ocurrencia.
    seen: dict[str, TransactionIn] = {}
    intra_dups: list[str] = []
    for t in txns:
        if t.transaction_id in seen:
            intra_dups.append(t.transaction_id)
        else:
            seen[t.transaction_id] = t

    ids = list(seen.keys())
    with b.sqlite_lock:
        # 2. Detectar cuales ya existen en la base.
        placeholders = ",".join("?" * len(ids))
        existing = {
            r["transaction_id"]
            for r in b.sqlite.execute(
                f"SELECT transaction_id FROM transactions "
                f"WHERE transaction_id IN ({placeholders})",
                ids,
            ).fetchall()
        }
        to_insert = [
            (
                t.transaction_id,
                t.timestamp.strftime(_TS_FMT),
                t.user_id,
                t.merchant_id,
                t.amount,
                t.category,
                t.country_code,
                t.status,
            )
            for tid, t in seen.items()
            if tid not in existing
        ]
        # 3. Insertar en una sola transaccion.
        if to_insert:
            b.sqlite.execute("BEGIN")
            b.sqlite.executemany(
                "INSERT INTO transactions "
                "(transaction_id, timestamp, user_id, merchant_id, amount, "
                " category, country_code, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                to_insert,
            )
            b.sqlite.commit()

    duplicate_ids = intra_dups + sorted(existing)
    return len(to_insert), duplicate_ids
