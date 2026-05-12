"""Polars implementation of the 8 queries.

Uses scan_parquet for lazy/streaming execution. Each q* function takes
the LazyFrame and returns an eager pl.DataFrame (collected).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import polars as pl

NAME = "polars"


def load(path: str | Path) -> pl.LazyFrame:
    return pl.scan_parquet(str(path))


def q1(lf: pl.LazyFrame) -> pl.DataFrame:
    return (
        lf.group_by("country_code")
        .agg(pl.len().alias("n_transactions"))
        .sort(["n_transactions", "country_code"], descending=[True, False])
        .collect()
    )


def q2(lf: pl.LazyFrame) -> pl.DataFrame:
    return (
        lf.group_by("category")
        .agg(
            pl.col("amount").mean().alias("avg_amount"),
            pl.col("amount").min().alias("min_amount"),
            pl.col("amount").max().alias("max_amount"),
        )
        .sort("category")
        .collect()
    )


def q3(lf: pl.LazyFrame) -> pl.DataFrame:
    return (
        lf.group_by("user_id")
        .agg(
            pl.col("amount").sum().alias("total_amount"),
            pl.len().alias("n_transactions"),
        )
        .sort(["total_amount", "user_id"], descending=[True, False])
        .head(10)
        .collect()
    )


def q4(lf: pl.LazyFrame) -> pl.DataFrame:
    return (
        lf.filter(pl.col("status") == "failed")
        .with_columns(pl.col("timestamp").dt.hour().alias("hour"))
        .group_by("hour")
        .agg(pl.len().alias("n_failed"))
        .sort("hour")
        .collect()
    )


def q5(lf: pl.LazyFrame) -> pl.DataFrame:
    max_ts = lf.select(pl.col("timestamp").max()).collect().item()
    cutoff = max_ts - timedelta(days=30)
    return (
        lf.filter(
            (pl.col("amount") > 500)
            & (pl.col("country_code").is_in(["MX", "CO"]))
            & (pl.col("timestamp") >= cutoff)
        )
        .sort("transaction_id")
        .collect()
    )


def q6(lf: pl.LazyFrame) -> pl.DataFrame:
    counts = (
        lf.group_by(["country_code", "category"])
        .agg(
            pl.len().alias("n_transactions"),
            pl.col("amount").mean().alias("avg_amount"),
        )
        .sort(
            ["country_code", "n_transactions", "category"],
            descending=[False, True, False],
        )
    )
    return counts.unique(subset="country_code", keep="first").sort("country_code").collect()


def q7(lf: pl.LazyFrame) -> pl.DataFrame:
    return (
        lf.filter(pl.col("status") == "failed")
        .group_by("user_id")
        .agg(pl.len().alias("n_failed"))
        .filter(pl.col("n_failed") > 5)
        .sort(["n_failed", "user_id"], descending=[True, False])
        .collect()
    )


def q8(lf: pl.LazyFrame) -> pl.DataFrame:
    return (
        lf.with_columns(pl.col("timestamp").dt.truncate("1d").alias("day"))
        .group_by(["day", "category"])
        .agg(pl.col("amount").mean().alias("avg_amount"))
        .sort(["day", "category"])
        .collect()
    )
