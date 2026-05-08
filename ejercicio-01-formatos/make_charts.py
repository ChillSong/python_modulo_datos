"""Genera las graficas de barras del reporte a partir de los JSON de results/.

Uso:
    python make_charts.py
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
CHARTS_DIR = HERE / "charts"
SIZES = ["100k", "500k", "1m"]
FORMAT_ORDER = ["csv", "jsonl", "parquet_none", "parquet_snappy", "parquet_gzip"]


def load_all() -> dict[str, dict[str, dict]]:
    out: dict[str, dict[str, dict]] = {}
    for size in SIZES:
        payload = json.loads((RESULTS_DIR / f"results_{size}.json").read_text())
        out[size] = {r["format"]: r for r in payload["results"]}
    return out


def grouped_bar(data, title, ylabel, value_fn, filename, log=False):
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(FORMAT_ORDER))
    width = 0.27
    for i, size in enumerate(SIZES):
        values = [value_fn(data[size][fmt]) for fmt in FORMAT_ORDER]
        ax.bar(x + (i - 1) * width, values, width, label=size)
    ax.set_xticks(x)
    ax.set_xticklabels(FORMAT_ORDER, rotation=15)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(title="Escala")
    if log:
        ax.set_yscale("log")
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    out_path = CHARTS_DIR / filename
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"  -> {out_path.relative_to(HERE.parent)}")


def main() -> None:
    CHARTS_DIR.mkdir(exist_ok=True)
    data = load_all()
    print("Generando graficas...")
    grouped_bar(
        data,
        title="Tiempo de lectura completa por formato",
        ylabel="Segundos (escala log)",
        value_fn=lambda r: r["read_full_seconds"],
        filename="read_full_time.png",
        log=True,
    )
    grouped_bar(
        data,
        title="Tiempo de lectura selectiva (amount + category)",
        ylabel="Segundos (escala log)",
        value_fn=lambda r: r["read_selective_seconds"],
        filename="read_selective_time.png",
        log=True,
    )
    grouped_bar(
        data,
        title="Tamaño en disco por formato",
        ylabel="Megabytes",
        value_fn=lambda r: r["file_size_bytes"] / 1e6,
        filename="file_size.png",
    )
    grouped_bar(
        data,
        title="Tiempo de escritura (promedio de 3 repeticiones)",
        ylabel="Segundos (escala log)",
        value_fn=lambda r: r["write_seconds_mean"],
        filename="write_time.png",
        log=True,
    )


if __name__ == "__main__":
    main()
