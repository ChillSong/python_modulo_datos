"""Capa de transformacion / validacion.

Aplica las reglas de negocio del schema fijo del modulo (E1 paso 1).
Los registros invalidos van a quarantine/YYYY-MM-DD.jsonl con el motivo.
Los validos pasan a la capa de carga.

Standalone: no importa de data_source ni de ninguna capa externa.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

CATEGORIES = {
    "Food", "Travel", "Electronics", "Health", "Entertainment",
    "Retail", "Transport", "Education", "Services", "Other",
}
COUNTRIES = {
    "MX", "CO", "BR", "AR", "CL", "PE", "EC", "VE",
    "BO", "PY", "UY", "CR", "GT", "PA", "DO",
}
STATUSES = {"completed", "failed", "pending"}

REQUIRED_FIELDS = (
    "transaction_id", "timestamp", "user_id", "merchant_id",
    "amount", "category", "country_code", "status",
)


def _is_uuid4(s) -> bool:
    if not isinstance(s, str):
        return False
    try:
        u = uuid.UUID(s)
    except (ValueError, AttributeError):
        return False
    return u.version == 4


def _validate(record: dict, now: datetime) -> str | None:
    for f in REQUIRED_FIELDS:
        if record.get(f) is None or record.get(f) == "":
            return "null_field"

    if not _is_uuid4(record["transaction_id"]):
        return "invalid_uuid"

    ts_raw = record["timestamp"]
    try:
        ts = datetime.fromisoformat(ts_raw) if isinstance(ts_raw, str) else None
    except ValueError:
        ts = None
    if ts is None:
        return "invalid_timestamp"
    if ts > now + timedelta(hours=1):
        return "future_timestamp"

    amount = record["amount"]
    if not isinstance(amount, (int, float)) or isinstance(amount, bool):
        return "invalid_amount_type"
    if not (0.01 <= amount <= 5000.00):
        return "amount_out_of_range"

    if record["category"] not in CATEGORIES:
        return "invalid_category"

    if record["country_code"] not in COUNTRIES:
        return "invalid_country"

    if record["status"] not in STATUSES:
        return "invalid_status"

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
    now = now or datetime.now()
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    quarantine_file = quarantine_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"

    valid: list[dict] = []
    rejected_counts: dict[str, int] = {}

    with quarantine_file.open("a") as fh:
        for record in records:
            reason = _validate(record, now)
            if reason is None:
                valid.append(record)
            else:
                rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
                fh.write(json.dumps({
                    "rejected_at": now.isoformat(timespec="seconds"),
                    "reason": reason,
                    "record": record,
                }) + "\n")

    return valid, rejected_counts
