"""Fuente simulada de transacciones nuevas.

Genera un batch (entre 100 y 1000 filas segun --batch-size) con el schema fijo
del modulo. Inyecta errores deliberados controlados por --error-rate para
ejercitar la capa de validacion del pipeline (transform.py).

Los registros se emiten en formatos "crudos" (timestamps con espacio, no ISO;
country_code en minusculas; amount con muchos decimales) para que la capa de
extraccion tenga algo que normalizar.

Uso como libreria:
    from data_source import simulate_batch
    batch = simulate_batch(size=500, error_rate=0.1, seed=42)

Uso como CLI (escribe JSONL):
    python data_source.py --batch-size 500 --error-rate 0.1 --seed 42 \\
        --output inbox/batch.jsonl
"""

from __future__ import annotations

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# Dominios fijos del schema del modulo (E1).
CATEGORIES = [
    "Food", "Travel", "Electronics", "Health", "Entertainment",
    "Retail", "Transport", "Education", "Services", "Other",
]
COUNTRIES = [
    "MX", "CO", "BR", "AR", "CL", "PE", "EC", "VE", "BO", "PY",
    "UY", "CR", "GT", "PA", "DO",
]
STATUSES = ["completed", "failed", "pending"]

# Tipos de error que data_source.py puede inyectar. Cada uno produce un fallo
# distinto en transform.py (la rubrica pide que TODOS los tipos lleguen a
# quarantine con motivo).
ERROR_TYPES = [
    "amount_negative",
    "amount_too_high",
    "invalid_category",
    "invalid_country",
    "future_timestamp",
    "null_field",
    "malformed_uuid",
]


def _gen_uuid4(rng: random.Random) -> str:
    """UUID4 deterministico a partir del rng (no usa os.urandom)."""
    b = bytearray(rng.randbytes(16))
    b[6] = (b[6] & 0x0f) | 0x40  # version 4
    b[8] = (b[8] & 0x3f) | 0x80  # variante RFC 4122
    return str(uuid.UUID(bytes=bytes(b)))


def _valid_record(rng: random.Random, now: datetime) -> dict:
    """Registro 'crudo' bien formado. Formatos sin normalizar (extract los pulira)."""
    # Timestamp uniformemente en el ultimo ano (mismo rango que el dataset del E1).
    seconds_back = rng.randint(0, 365 * 24 * 3600)
    ts = now - timedelta(seconds=seconds_back)
    # Formato con espacio (NO ISO 'T'). extract lo normaliza.
    timestamp_raw = ts.strftime("%Y-%m-%d %H:%M:%S")

    # country_code en minusculas para que extract lo pase a mayusculas.
    country_raw = rng.choice(COUNTRIES).lower()

    # amount con mas de 2 decimales para que extract lo redondee.
    amount_raw = round(rng.uniform(0.01, 5000.00), 5)

    return {
        "transaction_id": _gen_uuid4(rng),
        "timestamp": timestamp_raw,
        "user_id": rng.randint(1, 50_000),
        "merchant_id": rng.randint(1, 10_000),
        "amount": amount_raw,
        "category": rng.choice(CATEGORIES),
        "country_code": country_raw,
        "status": rng.choices(STATUSES, weights=[85, 10, 5], k=1)[0],
    }


def _inject_error(record: dict, error_type: str, rng: random.Random, now: datetime) -> dict:
    """Reemplaza un campo del registro para producir un error de validacion conocido."""
    bad = dict(record)
    if error_type == "amount_negative":
        bad["amount"] = -round(rng.uniform(0.01, 100.0), 2)
    elif error_type == "amount_too_high":
        bad["amount"] = round(rng.uniform(5_001.0, 100_000.0), 2)
    elif error_type == "invalid_category":
        bad["category"] = rng.choice(["Groceries", "Crypto", "Gambling", "Unknown"])
    elif error_type == "invalid_country":
        bad["country_code"] = rng.choice(["us", "es", "fr", "zz"])
    elif error_type == "future_timestamp":
        future = now + timedelta(hours=rng.uniform(2, 48))
        bad["timestamp"] = future.strftime("%Y-%m-%d %H:%M:%S")
    elif error_type == "null_field":
        field = rng.choice(["amount", "category", "country_code", "user_id"])
        bad[field] = None
    elif error_type == "malformed_uuid":
        bad["transaction_id"] = rng.choice([
            "not-a-uuid",
            "1234",
            "ffffffff-ffff-ffff-ffff-ffffffffffff",  # no es v4 (bits incorrectos)
        ])
    else:
        raise ValueError(f"error_type desconocido: {error_type}")
    return bad


def simulate_batch(
    size: int,
    error_rate: float,
    seed: int,
    *,
    now: datetime | None = None,
) -> list[dict]:
    """Genera un batch determinista de `size` registros con `error_rate` errores.

    El orden de inyeccion de errores es deterministico para un mismo (size,
    error_rate, seed), lo que garantiza idempotencia: dos corridas con los
    mismos parametros producen exactamente el mismo batch y, por la PK
    transaction_id, el mismo resultado final en la base.
    """
    if not 100 <= size <= 1000:
        raise ValueError("size debe estar entre 100 y 1000 (limite del PDF).")
    if not 0.0 <= error_rate <= 1.0:
        raise ValueError("error_rate debe estar entre 0.0 y 1.0.")

    rng = random.Random(seed)
    now = now or datetime.now()
    batch: list[dict] = []
    n_errors = int(round(size * error_rate))

    # Decide de antemano cuales indices llevaran error y de que tipo.
    error_indices = set(rng.sample(range(size), n_errors)) if n_errors else set()
    error_kinds = [rng.choice(ERROR_TYPES) for _ in range(size)]

    for i in range(size):
        rec = _valid_record(rng, now)
        if i in error_indices:
            rec = _inject_error(rec, error_kinds[i], rng, now)
        batch.append(rec)

    return batch


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fuente simulada de transacciones.")
    p.add_argument("--batch-size", type=int, default=500,
                   help="Cantidad de registros por batch (100..1000). Default 500.")
    p.add_argument("--error-rate", type=float, default=0.1,
                   help="Fraccion de registros con error inyectado (0.0..1.0). Default 0.1.")
    p.add_argument("--seed", type=int, default=42,
                   help="Semilla del generador (default 42).")
    p.add_argument("--output", type=Path, required=True,
                   help="Archivo JSONL de salida (una linea por registro).")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    batch = simulate_batch(args.batch_size, args.error_rate, args.seed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w") as fh:
        for rec in batch:
            fh.write(json.dumps(rec) + "\n")
    print(f"Wrote {len(batch)} records to {args.output}")


if __name__ == "__main__":
    main()
