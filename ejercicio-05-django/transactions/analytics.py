"""Analytics con DuckDB sobre el Parquet del E1.

El PDF del E5 permite explicitamente usar DuckDB directo para `/analytics/*`
("DRF no obliga a usar el ORM para todo"). Mantenemos el mismo reparto que el
E4: los agregados full-scan van a un motor columnar, no al ORM fila por fila.

La conexion DuckDB se abre UNA vez (singleton perezoso protegido por Lock) y se
reutiliza entre requests — nunca se abre por endpoint. La vista `txns` apunta al
Parquet de 1M filas; DuckDB no lo carga a memoria, lo escanea al vuelo.
"""

from __future__ import annotations

import threading
from pathlib import Path

import duckdb
from django.conf import settings

_conn: duckdb.DuckDBPyConnection | None = None
_lock = threading.Lock()


def _get_conn() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                parquet = Path(settings.E5_PARQUET)
                if not parquet.exists():
                    raise FileNotFoundError(
                        f"Parquet no encontrado para analytics: {parquet}. "
                        "Genera el dataset del E1 (ver README)."
                    )
                conn = duckdb.connect(":memory:")
                conn.execute(
                    "CREATE OR REPLACE VIEW txns AS "
                    f"SELECT * FROM read_parquet('{parquet}')"
                )
                _conn = conn
    return _conn


def summary() -> dict:
    conn = _get_conn()
    with _lock:
        n, total, avg = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(amount), 0), COALESCE(AVG(amount), 0) FROM txns"
        ).fetchone()
        by_country = conn.execute(
            "SELECT country_code, COUNT(*) AS n, SUM(amount) AS total "
            "FROM txns GROUP BY country_code ORDER BY n DESC, country_code ASC"
        ).fetchall()
        by_category = conn.execute(
            "SELECT category, COUNT(*) AS n, SUM(amount) AS total "
            "FROM txns GROUP BY category ORDER BY n DESC, category ASC"
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


def top_merchants(limit: int, country: str | None) -> list[dict]:
    sql = "SELECT merchant_id, COUNT(*) AS n, SUM(amount) AS total FROM txns "
    params: list = []
    if country is not None:
        sql += "WHERE country_code = ? "
        params.append(country)
    sql += "GROUP BY merchant_id ORDER BY total DESC, merchant_id ASC LIMIT ?"
    params.append(limit)
    conn = _get_conn()
    with _lock:
        rows = conn.execute(sql, params).fetchall()
    return [
        {"merchant_id": int(m), "n_transactions": int(n), "total_amount": round(float(t), 2)}
        for m, n, t in rows
    ]


def healthy() -> bool:
    try:
        conn = _get_conn()
        with _lock:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False
