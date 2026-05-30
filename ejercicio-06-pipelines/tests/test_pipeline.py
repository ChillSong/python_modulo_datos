"""Tests del pipeline ETL (E6).

Cubre la rubrica:
  - Capas separadas: extract normaliza sin rechazar; transform rechaza con motivo.
  - Validacion y cuarentena: un test por cada regla del PDF.
  - Idempotencia: dos corridas con los mismos parametros -> mismo estado final.
  - Reporte de ejecucion: estructura y cuadre de numeros.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Aseguramos que los modulos del ejercicio esten en el path al correr pytest
# desde la raiz del repo o desde la carpeta del ejercicio.
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

from data_source import simulate_batch  # noqa: E402
from extract import extract, extract_record  # noqa: E402
from load import load  # noqa: E402
from pipeline import run as run_pipeline  # noqa: E402
from transform import transform  # noqa: E402

NOW = datetime(2026, 5, 29, 12, 0, 0)


def _valid_raw(**overrides) -> dict:
    """Registro 'crudo' valido base — los tests sobreescriben campos puntuales."""
    base = {
        "transaction_id": str(uuid.uuid4()),
        "timestamp": "2026-01-15 10:30:00",
        "user_id": 42,
        "merchant_id": 7,
        "amount": 99.95678,
        "category": "Food",
        "country_code": "mx",  # extract lo pasa a mayusculas
        "status": "completed",
    }
    base.update(overrides)
    return base


# -- Extract ----------------------------------------------------------------

def test_extract_normalizes_timestamp_to_iso():
    rec = extract_record(_valid_raw(timestamp="2026-01-15 10:30:00"))
    assert rec["timestamp"] == "2026-01-15T10:30:00"


def test_extract_uppercases_country():
    rec = extract_record(_valid_raw(country_code="br"))
    assert rec["country_code"] == "BR"


def test_extract_rounds_amount_to_two_decimals():
    rec = extract_record(_valid_raw(amount=123.456789))
    assert rec["amount"] == 123.46


def test_extract_passes_through_unparseable_timestamp():
    # Si extract no puede parsear, transform lo rechazara como invalid_timestamp.
    rec = extract_record(_valid_raw(timestamp="not-a-date"))
    assert rec["timestamp"] == "not-a-date"


# -- Transform — una regla por test ----------------------------------------

@pytest.fixture
def quarantine_dir(tmp_path):
    d = tmp_path / "quarantine"
    d.mkdir()
    return d


def _run_transform(records, quarantine_dir):
    extracted = extract(records)
    return transform(extracted, quarantine_dir=quarantine_dir, now=NOW)


def test_transform_accepts_valid(quarantine_dir):
    valid, rejected = _run_transform([_valid_raw()], quarantine_dir)
    assert len(valid) == 1
    assert rejected == {}


def test_transform_rejects_amount_negative(quarantine_dir):
    valid, rejected = _run_transform([_valid_raw(amount=-5.0)], quarantine_dir)
    assert valid == []
    assert rejected == {"amount_out_of_range": 1}


def test_transform_rejects_amount_too_high(quarantine_dir):
    valid, rejected = _run_transform([_valid_raw(amount=9_999.0)], quarantine_dir)
    assert rejected == {"amount_out_of_range": 1}


def test_transform_rejects_invalid_category(quarantine_dir):
    valid, rejected = _run_transform([_valid_raw(category="Groceries")], quarantine_dir)
    assert rejected == {"invalid_category": 1}


def test_transform_rejects_invalid_country(quarantine_dir):
    valid, rejected = _run_transform([_valid_raw(country_code="us")], quarantine_dir)
    assert rejected == {"invalid_country": 1}


def test_transform_rejects_future_timestamp(quarantine_dir):
    future = (NOW + timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
    valid, rejected = _run_transform([_valid_raw(timestamp=future)], quarantine_dir)
    assert rejected == {"future_timestamp": 1}


def test_transform_accepts_recent_future_within_1h(quarantine_dir):
    # 30 minutos en el futuro no debe ser rechazado (el limite es 1h).
    soon = (NOW + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
    valid, rejected = _run_transform([_valid_raw(timestamp=soon)], quarantine_dir)
    assert len(valid) == 1
    assert rejected == {}


def test_transform_rejects_malformed_uuid(quarantine_dir):
    valid, rejected = _run_transform([_valid_raw(transaction_id="not-a-uuid")], quarantine_dir)
    assert rejected == {"invalid_uuid": 1}


def test_transform_rejects_null_field(quarantine_dir):
    valid, rejected = _run_transform([_valid_raw(amount=None)], quarantine_dir)
    assert rejected == {"null_field": 1}


def test_quarantine_file_records_reason_and_record(quarantine_dir):
    bad = _valid_raw(category="Groceries")
    _run_transform([bad], quarantine_dir)
    qfile = quarantine_dir / f"{NOW.strftime('%Y-%m-%d')}.jsonl"
    assert qfile.exists()
    line = json.loads(qfile.read_text().strip())
    assert line["reason"] == "invalid_category"
    assert line["record"]["category"] == "Groceries"


# -- Load -------------------------------------------------------------------

@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def _read_count(db_path):
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    finally:
        con.close()


def test_load_inserts_new_rows(db_path):
    records = [extract_record(_valid_raw()) for _ in range(5)]
    stats = load(records, db_path)
    assert stats == {"received": 5, "inserted": 5, "duplicates_skipped": 0}
    assert _read_count(db_path) == 5


def test_load_skips_duplicates_by_transaction_id(db_path):
    txn = extract_record(_valid_raw())
    load([txn], db_path)
    # Reenviar el mismo transaction_id: INSERT OR IGNORE lo descarta.
    stats = load([txn], db_path)
    assert stats == {"received": 1, "inserted": 0, "duplicates_skipped": 1}
    assert _read_count(db_path) == 1


def test_load_is_atomic_on_error(db_path):
    """Si una fila revienta a mitad, ninguna queda en la base."""
    good = extract_record(_valid_raw())
    # Forzamos un ProgrammingError: falta una clave que el INSERT espera bindear.
    # (Una NOT NULL violation NO sirve aqui: INSERT OR IGNORE la suprime
    #  silenciosamente, lo cual es deseable en el flujo normal.)
    bad = extract_record(_valid_raw())
    del bad["amount"]
    with pytest.raises(sqlite3.Error):
        load([good, bad], db_path)
    assert _read_count(db_path) == 0


# -- Pipeline end-to-end ----------------------------------------------------

def test_pipeline_run_report_structure(tmp_path):
    report = run_pipeline(
        batch_size=200, error_rate=0.1, seed=1,
        db_path=tmp_path / "db" / "txns.db",
        quarantine_dir=tmp_path / "quarantine",
        results_dir=tmp_path / "results",
        now=NOW,
    )
    # Estructura que pide el PDF.
    for key in ("run_id", "started_at", "total_seconds", "source",
                "extracted", "valid", "rejected", "loaded", "db",
                "quarantine_file"):
        assert key in report
    # Los numeros cuadran: extracted = valid + rejected.total
    assert report["extracted"] == report["valid"] + report["rejected"]["total"]
    # Inserted + duplicates_skipped = received (que aqui = valid).
    assert (
        report["loaded"]["inserted"] + report["loaded"]["duplicates_skipped"]
        == report["loaded"]["received"]
    )
    # El JSON se escribio a disco.
    out = Path(report["report_file"])
    assert out.exists()
    assert json.loads(out.read_text())["run_id"] == report["run_id"]


def test_pipeline_is_idempotent(tmp_path):
    """Correr el pipeline dos veces con los mismos parametros = mismo estado final."""
    db = tmp_path / "db" / "txns.db"
    q = tmp_path / "quarantine"
    r = tmp_path / "results"
    kwargs = dict(batch_size=300, error_rate=0.15, seed=7,
                  db_path=db, quarantine_dir=q, results_dir=r, now=NOW)

    first = run_pipeline(**kwargs)
    rows_after_first = _read_count(db)

    second = run_pipeline(**kwargs)
    rows_after_second = _read_count(db)

    # La segunda corrida no inserta nada nuevo.
    assert rows_after_first == rows_after_second
    assert second["loaded"]["inserted"] == 0
    assert second["loaded"]["duplicates_skipped"] == first["loaded"]["inserted"]
    # Y rechazo el mismo numero de filas (mismas razones).
    assert first["rejected"] == second["rejected"]


def test_pipeline_rejects_all_error_types_in_quarantine(tmp_path):
    """Con error_rate alto los 7 tipos de error aparecen en la cuarentena."""
    report = run_pipeline(
        batch_size=1000, error_rate=1.0, seed=99,
        db_path=tmp_path / "db" / "txns.db",
        quarantine_dir=tmp_path / "quarantine",
        results_dir=tmp_path / "results",
        now=NOW,
    )
    reasons = set(report["rejected"]["by_reason"].keys())
    # Todos los 7 tipos del PDF deben estar representados.
    expected = {
        "amount_out_of_range",
        "invalid_category",
        "invalid_country",
        "future_timestamp",
        "null_field",
        "invalid_uuid",
    }
    assert expected.issubset(reasons)


# -- data_source determinismo ----------------------------------------------

def test_simulate_batch_is_deterministic():
    a = simulate_batch(100, 0.1, seed=42, now=NOW)
    b = simulate_batch(100, 0.1, seed=42, now=NOW)
    assert a == b


def test_simulate_batch_size_bounds():
    with pytest.raises(ValueError):
        simulate_batch(50, 0.1, seed=1)
    with pytest.raises(ValueError):
        simulate_batch(1500, 0.1, seed=1)
