"""Mediciones de tiempo, tamaño y pico de RAM por formato.

tracemalloc solo rastrea allocations de Python — la I/O y los buffers en C
(p.ej. pyarrow) no aparecen. Es la herramienta que pide el PDF y sirve para
comparar formatos entre si, pero no es un proxy fiel del RSS del proceso.
"""

from __future__ import annotations

import gc
import time
import tracemalloc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd

from .formats import Format


@dataclass
class FormatResult:
    format: str
    write_seconds_mean: float
    write_seconds_runs: list[float] = field(default_factory=list)
    read_full_seconds: float = 0.0
    read_selective_seconds: float = 0.0
    file_size_bytes: int = 0
    read_peak_memory_bytes: int = 0


def _time(fn: Callable[[], object]) -> float:
    gc.collect()
    t0 = time.perf_counter()
    fn()
    return time.perf_counter() - t0


def _peak_memory(fn: Callable[[], object]) -> int:
    gc.collect()
    tracemalloc.start()
    fn()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return peak


def benchmark_format(
    df: pd.DataFrame,
    fmt: Format,
    output_dir: Path,
    selective_columns: Iterable[str],
    repetitions: int = 3,
) -> FormatResult:
    """Mide write (n reps), read full, read selectivo, tamaño y pico RAM."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"transactions{fmt.extension}"
    selective = list(selective_columns)

    write_runs: list[float] = []
    for _ in range(repetitions):
        if path.exists():
            path.unlink()
        write_runs.append(_time(lambda: fmt.write(df, path)))

    file_size = path.stat().st_size
    read_full_seconds = _time(lambda: fmt.read_full(path))
    read_selective_seconds = _time(lambda: fmt.read_columns(path, selective))
    peak_memory = _peak_memory(lambda: fmt.read_full(path))

    return FormatResult(
        format=fmt.name,
        write_seconds_mean=sum(write_runs) / len(write_runs),
        write_seconds_runs=write_runs,
        read_full_seconds=read_full_seconds,
        read_selective_seconds=read_selective_seconds,
        file_size_bytes=file_size,
        read_peak_memory_bytes=peak_memory,
    )
