"""Pandas implementation of the 8 queries.

Signature: load(path) -> pd.DataFrame; qN(df) -> pd.DataFrame.
All queries return a normalized pandas DataFrame (sorted, reset index)
so equivalence checking against the other engines is straightforward.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pandas as pd

NAME = "pandas"


def load(path: str | Path) -> pd.DataFrame:
    return pd.read_parquet(path)


def q1(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.groupby("country_code", as_index=False)
        .size()
        .rename(columns={"size": "n_transactions"})
        .sort_values(["n_transactions", "country_code"], ascending=[False, True])
        .reset_index(drop=True)
    )
    return out


def q2(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.groupby("category", as_index=False)["amount"]
        .agg(avg_amount="mean", min_amount="min", max_amount="max")
        .sort_values("category")
        .reset_index(drop=True)
    )
    return out


def q3(df: pd.DataFrame) -> pd.DataFrame:
    grouped = df.groupby("user_id", as_index=False).agg(
        total_amount=("amount", "sum"),
        n_transactions=("transaction_id", "count"),
    )
    out = grouped.sort_values(
        ["total_amount", "user_id"], ascending=[False, True]
    ).head(10).reset_index(drop=True)
    return out


def q4(df: pd.DataFrame) -> pd.DataFrame:
    failed = df.loc[df["status"] == "failed"].copy()
    failed["hour"] = failed["timestamp"].dt.hour
    out = (
        failed.groupby("hour", as_index=False)
        .size()
        .rename(columns={"size": "n_failed"})
        .sort_values("hour")
        .reset_index(drop=True)
    )
    return out


def q5(df: pd.DataFrame) -> pd.DataFrame:
    cutoff = df["timestamp"].max() - timedelta(days=30)
    mask = (
        (df["amount"] > 500)
        & (df["country_code"].isin(["MX", "CO"]))
        & (df["timestamp"] >= cutoff)
    )
    out = (
        df.loc[mask]
        .sort_values("transaction_id")
        .reset_index(drop=True)
    )
    return out


def q6(df: pd.DataFrame) -> pd.DataFrame:
    counts = (
        df.groupby(["country_code", "category"], as_index=False)
        .agg(n_transactions=("transaction_id", "count"),
             avg_amount=("amount", "mean"))
    )
    counts = counts.sort_values(
        ["country_code", "n_transactions", "category"],
        ascending=[True, False, True],
    )
    top = counts.drop_duplicates("country_code", keep="first")
    out = top.sort_values("country_code").reset_index(drop=True)
    return out


def q7(df: pd.DataFrame) -> pd.DataFrame:
    failed_per_user = (
        df.loc[df["status"] == "failed"]
        .groupby("user_id", as_index=False)
        .size()
        .rename(columns={"size": "n_failed"})
    )
    out = (
        failed_per_user.loc[failed_per_user["n_failed"] > 5]
        .sort_values(["n_failed", "user_id"], ascending=[False, True])
        .reset_index(drop=True)
    )
    return out


def q8(df: pd.DataFrame) -> pd.DataFrame:
    day = df["timestamp"].dt.floor("D")
    out = (
        df.assign(day=day)
        .groupby(["day", "category"], as_index=False)["amount"]
        .mean()
        .rename(columns={"amount": "avg_amount"})
        .sort_values(["day", "category"])
        .reset_index(drop=True)
    )
    return out
