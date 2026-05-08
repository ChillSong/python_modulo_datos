"""Read/write por formato.

Cada formato expone tres operaciones uniformes (write, read_full, read_columns)
para que el benchmark las invoque sin saber detalles del formato.

JSON Lines no soporta column pruning a nivel de formato (no tiene metadatos por
columna como Parquet), asi que `read_jsonl_columns` lee todo y proyecta — es
parte del comportamiento real que el benchmark debe medir, no un atajo.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def write_csv(df: pd.DataFrame, path: Path) -> None:
    df.to_csv(path, index=False)


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_csv_columns(path: Path, columns: Iterable[str]) -> pd.DataFrame:
    return pd.read_csv(path, usecols=list(columns))


def write_jsonl(df: pd.DataFrame, path: Path) -> None:
    df.to_json(path, orient="records", lines=True, date_format="iso")


def read_jsonl(path: Path) -> pd.DataFrame:
    return pd.read_json(path, lines=True)


def read_jsonl_columns(path: Path, columns: Iterable[str]) -> pd.DataFrame:
    df = pd.read_json(path, lines=True)
    return df[list(columns)]


def _write_parquet(df: pd.DataFrame, path: Path, compression: str | None) -> None:
    table = pa.Table.from_pandas(df, preserve_index=False)
    pq.write_table(table, path, compression=compression)


def write_parquet_none(df: pd.DataFrame, path: Path) -> None:
    _write_parquet(df, path, compression="none")


def write_parquet_snappy(df: pd.DataFrame, path: Path) -> None:
    _write_parquet(df, path, compression="snappy")


def write_parquet_gzip(df: pd.DataFrame, path: Path) -> None:
    _write_parquet(df, path, compression="gzip")


def read_parquet(path: Path) -> pd.DataFrame:
    return pq.read_table(path).to_pandas()


def read_parquet_columns(path: Path, columns: Iterable[str]) -> pd.DataFrame:
    return pq.read_table(path, columns=list(columns)).to_pandas()


@dataclass(frozen=True)
class Format:
    name: str
    extension: str
    write: Callable[[pd.DataFrame, Path], None]
    read_full: Callable[[Path], pd.DataFrame]
    read_columns: Callable[[Path, Iterable[str]], pd.DataFrame]


FORMATS: dict[str, Format] = {
    "csv": Format(
        name="csv",
        extension=".csv",
        write=write_csv,
        read_full=read_csv,
        read_columns=read_csv_columns,
    ),
    "jsonl": Format(
        name="jsonl",
        extension=".jsonl",
        write=write_jsonl,
        read_full=read_jsonl,
        read_columns=read_jsonl_columns,
    ),
    "parquet_none": Format(
        name="parquet_none",
        extension=".none.parquet",
        write=write_parquet_none,
        read_full=read_parquet,
        read_columns=read_parquet_columns,
    ),
    "parquet_snappy": Format(
        name="parquet_snappy",
        extension=".snappy.parquet",
        write=write_parquet_snappy,
        read_full=read_parquet,
        read_columns=read_parquet_columns,
    ),
    "parquet_gzip": Format(
        name="parquet_gzip",
        extension=".gzip.parquet",
        write=write_parquet_gzip,
        read_full=read_parquet,
        read_columns=read_parquet_columns,
    ),
}
