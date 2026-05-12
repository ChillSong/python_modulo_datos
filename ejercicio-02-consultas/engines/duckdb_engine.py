"""DuckDB implementation of the 8 queries.

Reads the Parquet file directly via `read_parquet()` (no pandas load).
Each query is an SQL string in QUERIES so benchmark.py can pull the
text for EXPLAIN ANALYZE on Q3/Q5/Q6.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

NAME = "duckdb"


QUERIES: dict[str, str] = {
    "q1": """
        SELECT country_code, COUNT(*) AS n_transactions
        FROM txns
        GROUP BY country_code
        ORDER BY n_transactions DESC, country_code ASC
    """,
    "q2": """
        SELECT category,
               AVG(amount) AS avg_amount,
               MIN(amount) AS min_amount,
               MAX(amount) AS max_amount
        FROM txns
        GROUP BY category
        ORDER BY category
    """,
    "q3": """
        SELECT user_id,
               SUM(amount) AS total_amount,
               COUNT(*)    AS n_transactions
        FROM txns
        GROUP BY user_id
        ORDER BY total_amount DESC, user_id ASC
        LIMIT 10
    """,
    "q4": """
        SELECT CAST(EXTRACT(hour FROM timestamp) AS INTEGER) AS hour,
               COUNT(*) AS n_failed
        FROM txns
        WHERE status = 'failed'
        GROUP BY hour
        ORDER BY hour
    """,
    "q5": """
        WITH bounds AS (SELECT MAX(timestamp) AS max_ts FROM txns)
        SELECT t.*
        FROM txns t, bounds b
        WHERE t.amount > 500
          AND t.country_code IN ('MX', 'CO')
          AND t.timestamp >= b.max_ts - INTERVAL 30 DAY
        ORDER BY t.transaction_id
    """,
    "q6": """
        WITH counts AS (
            SELECT country_code, category,
                   COUNT(*)  AS n_transactions,
                   AVG(amount) AS avg_amount
            FROM txns
            GROUP BY country_code, category
        ),
        ranked AS (
            SELECT *,
                   ROW_NUMBER() OVER (
                       PARTITION BY country_code
                       ORDER BY n_transactions DESC, category ASC
                   ) AS rn
            FROM counts
        )
        SELECT country_code, category, n_transactions, avg_amount
        FROM ranked
        WHERE rn = 1
        ORDER BY country_code
    """,
    "q7": """
        SELECT user_id, COUNT(*) AS n_failed
        FROM txns
        WHERE status = 'failed'
        GROUP BY user_id
        HAVING COUNT(*) > 5
        ORDER BY n_failed DESC, user_id ASC
    """,
    "q8": """
        SELECT CAST(date_trunc('day', timestamp) AS TIMESTAMP) AS day,
               category,
               AVG(amount) AS avg_amount
        FROM txns
        GROUP BY day, category
        ORDER BY day, category
    """,
}


def load(path: str | Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(":memory:")
    con.execute(
        f"CREATE OR REPLACE VIEW txns AS SELECT * FROM read_parquet('{path}')"
    )
    return con


def _run(con: duckdb.DuckDBPyConnection, qid: str) -> pd.DataFrame:
    return con.execute(QUERIES[qid]).df()


def explain_analyze(con: duckdb.DuckDBPyConnection, qid: str) -> str:
    rows = con.execute("EXPLAIN ANALYZE " + QUERIES[qid]).fetchall()
    return "\n".join(r[1] for r in rows)


def q1(con): return _run(con, "q1")
def q2(con): return _run(con, "q2")
def q3(con): return _run(con, "q3")
def q4(con): return _run(con, "q4")
def q5(con): return _run(con, "q5")
def q6(con): return _run(con, "q6")
def q7(con): return _run(con, "q7")
def q8(con): return _run(con, "q8")
