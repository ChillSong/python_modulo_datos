"""Capa de carga — insercion transaccional en SQLite.

INSERT OR IGNORE por transaction_id para deduplicacion natural.
Toda la insercion va en una sola transaccion; si falla, rollback completo.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent.parent  # ejercicio-08-final/
SCHEMA_PATH = HERE / "schema.sql"

_INSERT_SQL = (
    "INSERT OR IGNORE INTO transactions "
    "(transaction_id, timestamp, user_id, merchant_id, amount, "
    " category, country_code, status) "
    "VALUES (:transaction_id, :timestamp, :user_id, :merchant_id, :amount, "
    "        :category, :country_code, :status)"
)


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(schema_sql)
        con.commit()
    finally:
        con.close()


def load(records: Iterable[dict], db_path: Path) -> dict[str, int]:
    rows = list(records)
    init_db(db_path)
    con = sqlite3.connect(db_path)
    try:
        before = con.total_changes
        try:
            with con:
                con.executemany(_INSERT_SQL, rows)
        except sqlite3.Error:
            raise
        inserted = con.total_changes - before
    finally:
        con.close()

    return {
        "received": len(rows),
        "inserted": inserted,
        "duplicates_skipped": len(rows) - inserted,
    }
