"""Query engines for E2. Each engine exposes load() + q1..q8."""

from __future__ import annotations

from . import duckdb_engine, pandas_engine, polars_engine

ENGINES = {
    "pandas": pandas_engine,
    "duckdb": duckdb_engine,
    "polars": polars_engine,
}

QUERY_IDS = [f"q{i}" for i in range(1, 9)]
