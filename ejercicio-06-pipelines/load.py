"""Capa de carga — insercion transaccional en SQLite.

Usa `INSERT OR IGNORE` por `transaction_id` para que la PK natural haga el
trabajo de deduplicacion: una corrida que reciba los mismos datos no genera
duplicados. Toda la insercion va en una sola transaccion; si algo falla a la
mitad, se hace rollback y la base queda como estaba (rubrica: "transaccional").

El conteo de insertadas vs duplicadas se hace con `connection.total_changes`
antes y despues del `executemany`: la diferencia son las filas realmente
insertadas; el resto son las que IGNORE descarto por colision de PK.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema.sql"

_INSERT_SQL = (
    "INSERT OR IGNORE INTO transactions "
    "(transaction_id, timestamp, user_id, merchant_id, amount, "
    " category, country_code, status) "
    "VALUES (:transaction_id, :timestamp, :user_id, :merchant_id, :amount, "
    "        :category, :country_code, :status)"
)


def init_db(db_path: Path) -> None:
    """Crea la tabla si no existe. Idempotente."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SCHEMA_PATH.read_text()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(schema_sql)
        con.commit()
    finally:
        con.close()


def load(records: Iterable[dict], db_path: Path) -> dict[str, int]:
    """Inserta los registros validos en SQLite, transaccionalmente.

    Devuelve `{"received": N, "inserted": K, "duplicates_skipped": N-K}`.
    Si ocurre un error a mitad, la transaccion se revierte y se relanza.
    """
    rows = list(records)
    init_db(db_path)
    con = sqlite3.connect(db_path)
    try:
        before = con.total_changes
        try:
            with con:  # commit al salir; rollback si hay excepcion
                con.executemany(_INSERT_SQL, rows)
        except sqlite3.Error:
            # `with con` ya hizo rollback; relanzamos para que el orquestador
            # lo refleje en el reporte de la corrida.
            raise
        inserted = con.total_changes - before
    finally:
        con.close()

    return {
        "received": len(rows),
        "inserted": inserted,
        "duplicates_skipped": len(rows) - inserted,
    }
