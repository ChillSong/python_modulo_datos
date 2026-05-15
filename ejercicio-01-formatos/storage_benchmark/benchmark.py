"""Mediciones de tiempo, tamano y memoria por formato.

Dos metricas de memoria complementarias:

* `read_peak_memory_tracemalloc_bytes` — pico del heap de Python via
  tracemalloc. Util para comparar entre formatos, pero NO ve los buffers
  de C que asigna pyarrow, asi que para Parquet sale ~0.
* `read_rss_delta_bytes` — delta de RSS del proceso medido con
  `psutil.Process().memory_info().rss` antes y despues de la lectura.
  Incluye buffers de C y por lo tanto refleja la memoria real del proceso.

Lecturas y escrituras se repiten N veces (parametro `repetitions`) para
acotar la varianza del page cache del SO y del GC.
"""

from __future__ import annotations

import gc
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
import psutil

from .formats import Format


@dataclass
class FormatResult:
    format: str
    write_seconds_mean: float
    write_seconds_min: float
    write_seconds_runs: list[float] = field(default_factory=list)
    read_full_seconds_mean: float = 0.0
    read_full_seconds_min: float = 0.0
    read_full_seconds_runs: list[float] = field(default_factory=list)
    read_selective_seconds_mean: float = 0.0
    read_selective_seconds_min: float = 0.0
    read_selective_seconds_runs: list[float] = field(default_factory=list)
    file_size_bytes: int = 0
    read_peak_memory_tracemalloc_bytes: int = 0
    read_rss_delta_bytes: int = 0


def _time_repeated(fn: Callable[[], object], repetitions: int) -> list[float]:
    runs: list[float] = []
    for _ in range(repetitions):
        gc.collect()
        t0 = time.perf_counter()
        result = fn()
        runs.append(time.perf_counter() - t0)
        del result
    return runs


def _peak_tracemalloc(fn: Callable[[], object]) -> int:
    gc.collect()
    tracemalloc.start()
    result = fn()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    del result
    return peak


def _rss_delta(fn: Callable[[], object]) -> int:
    """Mide delta de RSS del proceso alrededor de fn().

    Captura buffers fuera del heap de Python (p.ej. pyarrow C buffers)
    que tracemalloc no puede ver.
    """
    proc = psutil.Process()
    gc.collect()
    rss_before = proc.memory_info().rss
    result = fn()
    rss_after = proc.memory_info().rss
    del result
    return rss_after - rss_before


def benchmark_format(
    df: pd.DataFrame,
    fmt: Format,
    output_dir: Path,
    selective_columns: Iterable[str],
    repetitions: int = 3,
) -> FormatResult:
    """Mide write/read full/read selectivo (n reps c/u), tamano, y memoria."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"transactions{fmt.extension}"
    selective = list(selective_columns)

    write_runs: list[float] = []
    for _ in range(repetitions):
        if path.exists():
            path.unlink()
        gc.collect()
        t0 = time.perf_counter()
        fmt.write(df, path)
        write_runs.append(time.perf_counter() - t0)

    file_size = path.stat().st_size
    read_full_runs = _time_repeated(lambda: fmt.read_full(path), repetitions)
    read_selective_runs = _time_repeated(
        lambda: fmt.read_columns(path, selective), repetitions
    )
    peak_tracemalloc = _peak_tracemalloc(lambda: fmt.read_full(path))
    rss_delta = _rss_delta(lambda: fmt.read_full(path))

    return FormatResult(
        format=fmt.name,
        write_seconds_mean=sum(write_runs) / len(write_runs),
        write_seconds_min=min(write_runs),
        write_seconds_runs=write_runs,
        read_full_seconds_mean=sum(read_full_runs) / len(read_full_runs),
        read_full_seconds_min=min(read_full_runs),
        read_full_seconds_runs=read_full_runs,
        read_selective_seconds_mean=sum(read_selective_runs) / len(read_selective_runs),
        read_selective_seconds_min=min(read_selective_runs),
        read_selective_seconds_runs=read_selective_runs,
        file_size_bytes=file_size,
        read_peak_memory_tracemalloc_bytes=peak_tracemalloc,
        read_rss_delta_bytes=rss_delta,
    )
