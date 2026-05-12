"""Benchmark de query engines del Ejercicio 2.

Ejecuta las 8 queries en pandas, DuckDB y polars, valida equivalencia
numerica entre engines, registra tiempo + pico de memoria, y captura
EXPLAIN ANALYZE para Q3/Q5/Q6.

Uso:
    python benchmark.py --output results/
    python benchmark.py --output results/ --repetitions 5
"""

from __future__ import annotations

import argparse
import gc
import json
import time
import tracemalloc
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from engines import ENGINES, QUERY_IDS
from engines import duckdb_engine

DEFAULT_PARQUET = (
    Path(__file__).resolve().parent.parent
    / "data" / "benchmark_1m" / "transactions.snappy.parquet"
)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Benchmark de query engines.")
    p.add_argument(
        "--parquet",
        type=Path,
        default=DEFAULT_PARQUET,
        help=f"Parquet a consultar (default: {DEFAULT_PARQUET}).",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parent / "results",
        help="Carpeta donde se guarda el JSON de resultados.",
    )
    p.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Repeticiones por query para promediar el tiempo (default 3).",
    )
    p.add_argument(
        "--engines",
        nargs="+",
        choices=list(ENGINES.keys()),
        default=list(ENGINES.keys()),
    )
    p.add_argument(
        "--queries",
        nargs="+",
        choices=QUERY_IDS,
        default=QUERY_IDS,
    )
    return p.parse_args()


def measure(fn: Callable[[], Any], repetitions: int) -> tuple[list[float], int, Any]:
    """Run fn `repetitions` times. Return per-run wall times, peak RAM (bytes), last result."""
    times: list[float] = []
    peak = 0
    result = None
    for i in range(repetitions):
        gc.collect()
        tracemalloc.start()
        t0 = time.perf_counter()
        result = fn()
        t1 = time.perf_counter()
        _, peak_run = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        times.append(t1 - t0)
        peak = max(peak, peak_run)
    return times, peak, result


def to_pandas(result: Any) -> pd.DataFrame:
    if isinstance(result, pd.DataFrame):
        return result
    if hasattr(result, "to_pandas"):
        return result.to_pandas()
    raise TypeError(f"Unsupported result type: {type(result)!r}")


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_datetime64_any_dtype(s):
            df[col] = s.astype("datetime64[ns]").astype("int64")
        elif pd.api.types.is_float_dtype(s):
            df[col] = s.astype("float64").round(6)
        elif pd.api.types.is_integer_dtype(s):
            df[col] = s.astype("int64")
        else:
            df[col] = s.astype("string")
    df = df.sort_values(list(df.columns)).reset_index(drop=True)
    return df


def equivalent(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    if a.shape != b.shape:
        return False
    if list(a.columns) != list(b.columns):
        # column order differs but same set is OK
        if set(a.columns) != set(b.columns):
            return False
        b = b[a.columns]
    for col in a.columns:
        sa, sb = a[col], b[col]
        if pd.api.types.is_float_dtype(sa) and pd.api.types.is_float_dtype(sb):
            if not np.allclose(sa, sb, rtol=1e-6, atol=1e-3, equal_nan=True):
                return False
        else:
            if not sa.equals(sb):
                return False
    return True


def run() -> None:
    args = parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    parquet = args.parquet.resolve()
    if not parquet.exists():
        raise FileNotFoundError(f"Parquet no encontrado: {parquet}")

    print(f"Parquet: {parquet}")
    print(f"Engines: {args.engines}")
    print(f"Queries: {args.queries}  |  repetitions={args.repetitions}\n")

    # Phase 1: load each engine once (load time recorded separately).
    handles: dict[str, Any] = {}
    load_times: dict[str, float] = {}
    for eng_name in args.engines:
        eng = ENGINES[eng_name]
        gc.collect()
        t0 = time.perf_counter()
        handles[eng_name] = eng.load(parquet)
        load_times[eng_name] = time.perf_counter() - t0
        print(f"[load] {eng_name}: {load_times[eng_name]:.3f}s")
    print()

    # Phase 2: per-query benchmark + equivalence.
    per_query: dict[str, dict[str, Any]] = {}
    normalized_results: dict[str, dict[str, pd.DataFrame]] = {}

    for qid in args.queries:
        per_query[qid] = {"engines": {}}
        normalized_results[qid] = {}
        for eng_name in args.engines:
            eng = ENGINES[eng_name]
            fn = getattr(eng, qid)
            handle = handles[eng_name]
            times, peak, result = measure(lambda: fn(handle), args.repetitions)
            df_native = to_pandas(result)
            normalized_results[qid][eng_name] = normalize(df_native)
            per_query[qid]["engines"][eng_name] = {
                "time_mean_seconds": float(np.mean(times)),
                "time_min_seconds": float(np.min(times)),
                "time_runs_seconds": [float(t) for t in times],
                "peak_python_memory_bytes": int(peak),
                "rows_returned": int(len(df_native)),
                "columns_returned": list(map(str, df_native.columns)),
            }
            print(
                f"[{qid}] {eng_name:7s} mean={np.mean(times):.4f}s "
                f"min={np.min(times):.4f}s peak_py={peak/1e6:.1f}MB "
                f"rows={len(df_native)}"
            )

        # Equivalence check: compare every engine pair.
        engs = list(normalized_results[qid].keys())
        pairs_ok = True
        details: dict[str, bool] = {}
        for i in range(len(engs)):
            for j in range(i + 1, len(engs)):
                a, b = engs[i], engs[j]
                ok = equivalent(normalized_results[qid][a], normalized_results[qid][b])
                details[f"{a}_vs_{b}"] = bool(ok)
                pairs_ok = pairs_ok and ok
        per_query[qid]["equivalent"] = bool(pairs_ok)
        per_query[qid]["equivalence_pairs"] = details
        print(f"        equivalence: {'OK' if pairs_ok else 'MISMATCH'}  {details}\n")

    # Phase 3: EXPLAIN ANALYZE on duckdb for Q3, Q5, Q6.
    explain: dict[str, str] = {}
    if "duckdb" in args.engines:
        for qid in ("q3", "q5", "q6"):
            if qid in args.queries:
                explain[qid] = duckdb_engine.explain_analyze(handles["duckdb"], qid)

    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "parquet": str(parquet),
        "engines": args.engines,
        "queries": args.queries,
        "repetitions": args.repetitions,
        "load_seconds": load_times,
        "per_query": per_query,
        "explain_analyze": explain,
    }
    out_path = args.output / "results.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nResultados -> {out_path}")
    if explain:
        explain_path = args.output / "explain_analyze.txt"
        explain_path.write_text(
            "\n\n".join(f"=== {q} ===\n{t}" for q, t in explain.items())
        )
        print(f"EXPLAIN     -> {explain_path}")


if __name__ == "__main__":
    run()
