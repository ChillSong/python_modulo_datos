"""Capa de extraccion — lee CSV y normaliza tipos.

Lee un CSV con headers que coinciden con el schema fijo del modulo (E1).
No valida reglas de negocio: solo normaliza formatos para que transform
pueda aplicar las reglas con confianza sobre tipos consistentes.

Normalizaciones:
  - timestamp -> ISO 8601 (varios formatos de entrada aceptados)
  - country_code -> mayusculas
  - amount -> float redondeado a 2 decimales
  - user_id / merchant_id -> int (CSV los lee como string)

Si un valor no se puede normalizar, se propaga el original; transform lo
rechazara con el motivo correcto.
"""

from __future__ import annotations

import csv
from pathlib import Path
from datetime import datetime
from typing import Iterable

_TIMESTAMP_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
)


def _normalize_timestamp(raw) -> str:
    if not isinstance(raw, str):
        return raw
    s = raw.strip()
    for fmt in _TIMESTAMP_FORMATS:
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).isoformat()
    except ValueError:
        return raw


def _normalize_amount(raw) -> float | str:
    try:
        return round(float(raw), 2)
    except (TypeError, ValueError):
        return raw


def _normalize_int(raw) -> int | str:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


def _normalize_country(raw) -> str:
    return raw.upper() if isinstance(raw, str) else raw


def extract_record(raw: dict) -> dict:
    return {
        "transaction_id": raw.get("transaction_id"),
        "timestamp": _normalize_timestamp(raw.get("timestamp")),
        "user_id": _normalize_int(raw.get("user_id")),
        "merchant_id": _normalize_int(raw.get("merchant_id")),
        "amount": _normalize_amount(raw.get("amount")),
        "category": raw.get("category"),
        "country_code": _normalize_country(raw.get("country_code")),
        "status": raw.get("status"),
    }


def extract(records: Iterable[dict]) -> list[dict]:
    return [extract_record(r) for r in records]


def read_csv(path: Path) -> list[dict]:
    """Lee un CSV y devuelve lista de dicts (raw, antes de normalizar)."""
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
