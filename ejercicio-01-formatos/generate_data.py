"""Genera el dataset sintético de transacciones para el modulo.

Schema fijo (definido en el PDF, no modificar):
    transaction_id  string (UUID4)        Único por fila
    timestamp       datetime              Rango de 1 año hacia atrás, uniforme
    user_id         int                   1 .. 50,000
    merchant_id     int                   1 .. 10,000
    amount          float                 0.01 .. 5,000.00
    category        string                10 valores
    country_code    string                15 países
    status          string                completed 85% / failed 10% / pending 5%

Uso:
    python generate_data.py --size 100k
    python generate_data.py --size 1m --seed 7 --output data/custom.csv
"""

from __future__ import annotations

import argparse
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SIZE_MAP = {"100k": 100_000, "500k": 500_000, "1m": 1_000_000}

CATEGORIES = [
    "Food", "Travel", "Electronics", "Health", "Entertainment",
    "Retail", "Transport", "Education", "Services", "Other",
]
COUNTRY_CODES = [
    "MX", "CO", "BR", "AR", "CL", "PE", "EC", "VE",
    "BO", "PY", "UY", "CR", "GT", "PA", "DO",
]
STATUSES = ["completed", "failed", "pending"]
STATUS_WEIGHTS = [0.85, 0.10, 0.05]


def generate(n_rows: int, seed: int = 42) -> pd.DataFrame:
    """Genera un DataFrame con n_rows transacciones siguiendo el schema fijo."""
    rng = np.random.default_rng(seed)

    # Timestamps uniformes en el último año (resolución de segundos).
    end = datetime.now().replace(microsecond=0)
    start = end - timedelta(days=365)
    span_seconds = int((end - start).total_seconds())
    offsets = rng.integers(0, span_seconds, size=n_rows, dtype=np.int64)
    timestamps = pd.to_datetime(start) + pd.to_timedelta(offsets, unit="s")

    # UUID4 — uuid.uuid4() cuesta ~1us, para 1M son ~1s. No hay shortcut estándar.
    transaction_ids = [str(uuid.uuid4()) for _ in range(n_rows)]

    user_ids = rng.integers(1, 50_001, size=n_rows, dtype=np.int32)
    merchant_ids = rng.integers(1, 10_001, size=n_rows, dtype=np.int32)

    # amount: float en [0.01, 5000.00], redondeado a 2 decimales.
    amounts = rng.uniform(0.01, 5_000.00, size=n_rows)
    amounts = np.round(amounts, 2)

    categories = rng.choice(CATEGORIES, size=n_rows)
    country_codes = rng.choice(COUNTRY_CODES, size=n_rows)
    statuses = rng.choice(STATUSES, size=n_rows, p=STATUS_WEIGHTS)

    return pd.DataFrame({
        "transaction_id": transaction_ids,
        "timestamp": timestamps,
        "user_id": user_ids,
        "merchant_id": merchant_ids,
        "amount": amounts,
        "category": categories,
        "country_code": country_codes,
        "status": statuses,
    })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Genera el dataset de transacciones.")
    parser.add_argument(
        "--size",
        required=True,
        choices=list(SIZE_MAP.keys()),
        help="Tamaño del dataset: 100k, 500k o 1m.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Semilla para reproducibilidad (default 42).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Ruta de salida. Default: data/transactions_<size>.csv (relativo a la raiz del repo).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    n_rows = SIZE_MAP[args.size]

    repo_root = Path(__file__).resolve().parent.parent
    output = args.output or (repo_root / "data" / f"transactions_{args.size}.csv")
    output.parent.mkdir(parents=True, exist_ok=True)

    print(f"Generando {n_rows:,} filas (seed={args.seed})...")
    t0 = time.perf_counter()
    df = generate(n_rows, seed=args.seed)
    t1 = time.perf_counter()
    print(f"  Generacion en memoria: {t1 - t0:.2f}s")

    print(f"Escribiendo CSV a {output}...")
    t0 = time.perf_counter()
    df.to_csv(output, index=False)
    t1 = time.perf_counter()
    size_mb = output.stat().st_size / (1024 * 1024)
    print(f"  Escritura: {t1 - t0:.2f}s · {size_mb:.1f} MB · {n_rows:,} filas")


if __name__ == "__main__":
    main()
