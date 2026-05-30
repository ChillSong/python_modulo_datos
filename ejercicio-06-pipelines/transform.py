"""Capa de transformacion / validacion.

Aplica las reglas de negocio del PDF (paso 3). Los registros que no las
cumplen no se descartan: van a quarantine/YYYY-MM-DD.jsonl con el motivo del
rechazo. Los validos pasan a la capa de carga.

Reglas:
  - amount entre 0.01 y 5000.00
  - category en el dominio de 10 valores
  - country_code en el dominio de 15 paises
  - timestamp no puede estar mas de 1h en el futuro
  - transaction_id debe ser un UUID4 valido
  - todos los campos obligatorios deben estar presentes (no nulls)

Cada motivo se reporta como una etiqueta corta que el reporte de ejecucion
agrega en `rejected.by_reason`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from data_source import CATEGORIES, COUNTRIES, STATUSES

CATEGORY_SET = set(CATEGORIES)
COUNTRY_SET = set(COUNTRIES)
STATUS_SET = set(STATUSES)

REQUIRED_FIELDS = (
    "transaction_id", "timestamp", "user_id", "merchant_id",
    "amount", "category", "country_code", "status",
)


def _is_uuid4(s) -> bool:
    """True si `s` es la representacion canonical de un UUID version 4."""
    if not isinstance(s, str):
        return False
    try:
        u = uuid.UUID(s)
    except (ValueError, AttributeError):
        return False
    return u.version == 4


def _validate(record: dict, now: datetime) -> str | None:
    """Devuelve None si el registro es valido; el motivo si no lo es.

    El orden importa: chequeamos primero los campos requeridos para que un
    null en `amount` no se reporte como `amount_out_of_range` (que es lo que
    pasaria si dejamos que la comparacion fallara mas adelante).
    """
    # 1) Campos requeridos presentes (no None, no string vacio).
    for field in REQUIRED_FIELDS:
        if record.get(field) is None or record.get(field) == "":
            return "null_field"

    # 2) transaction_id: UUID4 valido.
    if not _is_uuid4(record["transaction_id"]):
        return "invalid_uuid"

    # 3) timestamp parseable y no mas de 1h en el futuro.
    ts_raw = record["timestamp"]
    try:
        ts = datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else None
    except ValueError:
        ts = None
    if ts is None:
        return "invalid_timestamp"
    if ts > now + timedelta(hours=1):
        return "future_timestamp"

    # 4) amount numerico y dentro de rango.
    amount = record["amount"]
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return "invalid_amount_type"
    if not (0.01 <= amount <= 5000.00):
        return "amount_out_of_range"

    # 5) category en dominio.
    if record["category"] not in CATEGORY_SET:
        return "invalid_category"

    # 6) country_code en dominio.
    if record["country_code"] not in COUNTRY_SET:
        return "invalid_country"

    # 7) status en dominio (no figura como error inyectable, pero el schema lo
    # exige; si llega cualquier cosa rara la atrapamos aqui).
    if record["status"] not in STATUS_SET:
        return "invalid_status"

    # 8) IDs enteros y dentro de rango del schema fijo del modulo.
    if not (isinstance(record["user_id"], int) and 1 <= record["user_id"] <= 50_000):
        return "invalid_user_id"
    if not (isinstance(record["merchant_id"], int) and 1 <= record["merchant_id"] <= 10_000):
        return "invalid_merchant_id"

    return None


def transform(
    records: Iterable[dict],
    *,
    quarantine_dir: Path,
    now: datetime | None = None,
) -> tuple[list[dict], dict[str, int]]:
    """Valida los registros y aparta los invalidos a quarantine/YYYY-MM-DD.jsonl.

    Devuelve `(valid, rejected_counts)`. El archivo de cuarentena se abre en
    modo append: varias corridas del mismo dia comparten archivo.
    """
    now = now or datetime.now()
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    quarantine_file = quarantine_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"

    valid: list[dict] = []
    rejected_counts: dict[str, int] = {}

    with quarantine_file.open("a") as quarantine_fh:
        for record in records:
            reason = _validate(record, now)
            if reason is None:
                valid.append(record)
            else:
                rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
                quarantine_fh.write(json.dumps({
                    "rejected_at": now.isoformat(timespec="seconds"),
                    "reason": reason,
                    "record": record,
                }) + "\n")

    return valid, rejected_counts
