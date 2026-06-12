"""Construye la base SQLite que sirve la capa transaccional del E4.

Self-contained y reproducible desde un clon limpio: lee el Parquet de 1M
filas del E1, crea la tabla con el schema fijo del modulo y aplica los 2
indices secundarios que justificamos en el E3 (idx_user_timestamp e
idx_country_user).  No depende de E3 — el evaluador corre solo este script.

La ingesta es chunked con una transaccion por chunk (mismo patron que E3),
y deja la base en WAL para que la API pueda leer y escribir concurrentemente.

Lee el Parquet via DuckDB (que ya es dependencia del proyecto) para evitar
instalar pyarrow/pandas en el contenedor Docker y mantener la imagen < 300 MB.

Uso:
    uv run python ejercicio-04-sistema/setup_db.py
    uv run python ejercicio-04-sistema/setup_db.py --parquet <path> --db <path>
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
DEFAULT_PARQUET = HERE.parent / "data" / "benchmark_1m" / "transactions.snappy.parquet"
DEFAULT_DB = HERE / "db" / "transactions.db"

SCHEMA = """
CREATE TABLE transactions (
    transaction_id  TEXT    PRIMARY KEY,
    timestamp       TEXT    NOT NULL,
    user_id         INTEGER NOT NULL,
    merchant_id     INTEGER NOT NULL,
    amount          REAL    NOT NULL,
    category        TEXT    NOT NULL,
    country_code    TEXT    NOT NULL,
    status          TEXT    NOT NULL
);
"""

# Mismos indices del E3 (justificacion en ejercicio-03-sqlite/schema_design.md).
INDEXES = [
    "CREATE INDEX idx_txns_user_timestamp ON transactions (user_id, timestamp DESC)",
    "CREATE INDEX idx_txns_country_user   ON transactions (country_code, user_id)",
]

INSERT_SQL = (
    "INSERT INTO transactions "
    "(transaction_id, timestamp, user_id, merchant_id, amount, "
    " category, country_code, status) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
)

COLUMNS = [
    "transaction_id", "timestamp", "user_id", "merchant_id",
    "amount", "category", "country_code", "status",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Construye el SQLite transaccional del E4.")
    p.add_argument("--parquet", type=Path, default=DEFAULT_PARQUET,
                   help=f"Parquet de origen (default: {DEFAULT_PARQUET}).")
    p.add_argument("--db", type=Path, default=DEFAULT_DB,
                   help=f"Path del .db a crear (default: {DEFAULT_DB}).")
    p.add_argument("--chunk-size", type=int, default=50_000,
                   help="Filas por transaccion (default 50 000).")
    return p.parse_args()


def build(parquet: Path, db_path: Path, chunk_size: int) -> dict[str, float]:
    if db_path.exists():
        db_path.unlink()
    for suffix in ("-wal", "-shm"):
        side = db_path.with_name(db_path.name + suffix)
        if side.exists():
            side.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(db_path)
    cur = con.cursor()
    cur.execute("PRAGMA journal_mode = WAL")
    cur.execute("PRAGMA synchronous = NORMAL")
    cur.execute("PRAGMA cache_size = -200000")  # 200 MB
    cur.execute("PRAGMA temp_store = MEMORY")
    cur.executescript(SCHEMA)
    con.commit()

    # DuckDB reads Parquet natively — no pyarrow/pandas dependency needed.
    # strftime with %f produces 6-digit microseconds, matching the format
    # that the API uses when parsing timestamps from SQLite.
    duck = duckdb.connect(":memory:")
    duck.execute(f"CREATE VIEW txns AS SELECT * FROM read_parquet('{parquet}')")

    total = 0
    offset = 0
    t0 = time.perf_counter()
    while True:
        rows = duck.execute(
            f"SELECT transaction_id, "
            f"strftime(timestamp::TIMESTAMP, '%Y-%m-%dT%H:%M:%S.%f'), "
            f"user_id, merchant_id, amount, category, country_code, status "
            f"FROM txns LIMIT {chunk_size} OFFSET {offset}"
        ).fetchall()
        if not rows:
            break
        cur.execute("BEGIN")
        cur.executemany(INSERT_SQL, rows)
        con.commit()
        total += len(rows)
        offset += chunk_size
    duck.close()
    ingest_s = time.perf_counter() - t0

    t1 = time.perf_counter()
    for ddl in INDEXES:
        cur.execute(ddl)
    con.commit()
    index_s = time.perf_counter() - t1

    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    return {"rows": total, "ingest_seconds": ingest_s, "index_seconds": index_s}


def main() -> None:
    args = parse_args()
    if not args.parquet.exists():
        raise FileNotFoundError(
            f"Parquet no encontrado: {args.parquet}\n"
            "Genera primero el dataset de 1M filas con el E1:\n"
            "  uv run python ejercicio-01-formatos/generate_data.py --size 1m\n"
            "  uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m "
            "--formats parquet_snappy"
        )

    print(f"Parquet: {args.parquet}")
    print(f"DB:      {args.db}")
    m = build(args.parquet, args.db, args.chunk_size)
    print(f"Ingesta: {m['rows']:,} filas en {m['ingest_seconds']:.2f}s "
          f"({m['rows'] / m['ingest_seconds']:,.0f} filas/s)")
    print(f"Indices: 2 creados en {m['index_seconds']:.2f}s")
    print(f"Listo:   {args.db}")


if __name__ == "__main__":
    main()
