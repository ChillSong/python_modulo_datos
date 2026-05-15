"""CLI del benchmark de formatos.

Ejemplos:
    python benchmark_cli.py --size 100k
    python benchmark_cli.py --size 1m --formats csv parquet_gzip
    python benchmark_cli.py --size 500k --repetitions 5

Genera el dataset una sola vez en memoria (no se cuenta como tiempo de
escritura) y luego ejecuta el benchmark sobre cada formato pedido.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from generate_data import SIZE_MAP, generate
from storage_benchmark import FORMATS, benchmark_format


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark de formatos de almacenamiento.")
    parser.add_argument(
        "--size",
        required=True,
        choices=list(SIZE_MAP.keys()),
        help="Tamaño del dataset: 100k, 500k o 1m.",
    )
    parser.add_argument(
        "--formats",
        nargs="+",
        choices=list(FORMATS.keys()),
        default=list(FORMATS.keys()),
        help="Formatos a evaluar (default: todos).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Semilla para reproducibilidad (default 42).",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=3,
        help="Repeticiones de escritura, lectura completa y lectura selectiva (default 3).",
    )
    parser.add_argument(
        "--selective-columns",
        nargs="+",
        default=["amount", "category"],
        help="Columnas a usar en la lectura selectiva (default: amount category).",
    )
    return parser.parse_args()


def _format_result_line(result) -> str:
    return (
        f"  write_mean={result.write_seconds_mean:.3f}s "
        f"read_mean={result.read_full_seconds_mean:.3f}s "
        f"read_min={result.read_full_seconds_min:.3f}s "
        f"sel_mean={result.read_selective_seconds_mean:.3f}s "
        f"size={result.file_size_bytes / 1e6:.2f}MB "
        f"tracemalloc={result.read_peak_memory_tracemalloc_bytes / 1e6:.2f}MB "
        f"rss_delta={result.read_rss_delta_bytes / 1e6:.2f}MB"
    )


def main() -> None:
    args = parse_args()
    n_rows = SIZE_MAP[args.size]

    repo_root = Path(__file__).resolve().parent.parent
    data_dir = repo_root / "data" / f"benchmark_{args.size}"
    results_dir = Path(__file__).resolve().parent / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generando {n_rows:,} filas en memoria (seed={args.seed})...")
    df = generate(n_rows, seed=args.seed)
    print(f"  Listo. Schema: {list(df.columns)}")

    results = []
    for fmt_name in args.formats:
        fmt = FORMATS[fmt_name]
        print(f"[{fmt_name}] benchmarking...")
        result = benchmark_format(
            df,
            fmt,
            output_dir=data_dir,
            selective_columns=args.selective_columns,
            repetitions=args.repetitions,
        )
        print(_format_result_line(result))
        results.append(asdict(result))

    payload = {
        "size": args.size,
        "n_rows": n_rows,
        "seed": args.seed,
        "repetitions": args.repetitions,
        "selective_columns": args.selective_columns,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }

    out_path = results_dir / f"results_{args.size}.json"
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nResultados guardados en {out_path.relative_to(repo_root)}")


if __name__ == "__main__":
    main()
