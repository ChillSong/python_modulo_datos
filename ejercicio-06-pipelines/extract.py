"""Capa de extraccion — normaliza tipos y formatos.

Esta capa NO valida reglas de negocio: solo deja todo en un formato consistente
para que la capa de transformacion pueda aplicar las reglas con confianza.

Normalizaciones aplicadas (PDF, paso 2):
  - `timestamp` -> ISO 8601 (`YYYY-MM-DDTHH:MM:SS[.ffffff]`)
  - `country_code` -> mayusculas
  - `amount` -> redondeado a 2 decimales

Si un valor no se puede normalizar (timestamp ininteligible, amount no
numerico) se propaga el valor original tal cual; la capa de transformacion lo
rechazara con un motivo de "invalid_format". De esta forma extract nunca
descarta registros — esa decision es exclusiva de transform.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

# Formatos que aceptamos como entrada para `timestamp`. data_source emite el
# primero; los demas estan listados porque son los que aparecen en fuentes
# reales (CSVs externos, APIs, exports SQL). Si ninguno parsea, devolvemos el
# valor original y transform lo rechaza.
_TIMESTAMP_INPUT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%d/%m/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
)


def _normalize_timestamp(raw):
    """Parsea cualquier formato conocido y devuelve ISO 8601. Pasa el original si falla."""
    if not isinstance(raw, str):
        return raw  # transform lo rechazara
    s = raw.strip()
    for fmt in _TIMESTAMP_INPUT_FORMATS:
        try:
            return datetime.strptime(s, fmt).isoformat()
        except ValueError:
            continue
    # Ultimo intento: fromisoformat acepta variantes que strptime no.
    try:
        return datetime.fromisoformat(s).isoformat()
    except ValueError:
        return raw


def _normalize_country(raw):
    return raw.upper() if isinstance(raw, str) else raw


def _normalize_amount(raw):
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return round(float(raw), 2)
    return raw


def extract_record(raw: dict) -> dict:
    """Normaliza un registro. No muta el input."""
    return {
        "transaction_id": raw.get("transaction_id"),
        "timestamp": _normalize_timestamp(raw.get("timestamp")),
        "user_id": raw.get("user_id"),
        "merchant_id": raw.get("merchant_id"),
        "amount": _normalize_amount(raw.get("amount")),
        "category": raw.get("category"),
        "country_code": _normalize_country(raw.get("country_code")),
        "status": raw.get("status"),
    }


def extract(records: Iterable[dict]) -> list[dict]:
    """Normaliza un lote completo."""
    return [extract_record(r) for r in records]
