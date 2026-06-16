"""Suite de tests del E8 — Proyecto Final.

Cubre:
  API (usa datos reales si estan disponibles, skip si no):
    - happy path de los 7 endpoints
    - /anomalies: threshold alto -> lista vacia, threshold 0 -> lista
    - /anomalies: threshold invalido (negativo) -> 422
    - /users/*: 404 usuario inexistente
    - POST /transactions/batch: 422 datos invalidos
    - deduplicacion intra-lote y contra la base
    - SLA de latencia de /health (<50ms p95)
    - cache hit en analytics

  Pipeline (usa datos sinteticos en memoria, no requiere Parquet):
    - extract normaliza tipos desde CSV
    - transform rechaza cada tipo de error por separado
    - load es idempotente (mismos datos 2 veces = mismas filas)
    - pipeline end-to-end: reporte con estructura correcta
    - pipeline con CSV invalido: rechazados en cuarentena
"""

from __future__ import annotations

import csv
import statistics
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import DEFAULT_DB, DEFAULT_PARQUET
from app.main import app
from pipeline.extract import extract, extract_record, read_csv
from pipeline.load import load
from pipeline.pipeline import run as pipeline_run
from pipeline.transform import transform


# ===========================================================================
# Fixtures API
# ===========================================================================

@pytest.fixture(scope="module")
def client():
    if not DEFAULT_DB.exists() or not DEFAULT_PARQUET.exists():
        pytest.skip(
            "Faltan datos. Corre:\n"
            "  uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m --formats parquet_snappy\n"
            "  uv run python ejercicio-08-final/setup_db.py"
        )
    with TestClient(app) as c:
        yield c


def _valid_txn(**overrides) -> dict:
    txn = {
        "transaction_id": str(uuid.uuid4()),
        "timestamp": "2026-01-15T10:30:00",
        "user_id": 1,
        "merchant_id": 100,
        "amount": 49.99,
        "category": "Food",
        "country_code": "MX",
        "status": "completed",
    }
    txn.update(overrides)
    return txn


# ===========================================================================
# Tests API — happy path
# ===========================================================================

def test_health_ok(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["connections"] == {"duckdb": True, "sqlite": True}


def test_summary_ok(client):
    r = client.get("/analytics/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["n_transactions"] == 1_000_000
    assert len(body["by_country"]) == 15
    assert len(body["by_category"]) == 10


def test_top_merchants_ok(client):
    r = client.get("/analytics/top-merchants?limit=5")
    assert r.status_code == 200
    merchants = r.json()["merchants"]
    assert len(merchants) == 5
    totals = [m["total_amount"] for m in merchants]
    assert totals == sorted(totals, reverse=True)


def test_top_merchants_country_filter(client):
    r = client.get("/analytics/top-merchants?limit=3&country=MX")
    assert r.status_code == 200
    assert r.json()["country"] == "MX"
    assert len(r.json()["merchants"]) == 3


def test_user_transactions_ok(client):
    r = client.get("/users/1/transactions?page=1&page_size=10")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == 1
    assert body["n_returned"] <= 10
    ts = [t["timestamp"] for t in body["transactions"]]
    assert ts == sorted(ts, reverse=True)


def test_user_stats_ok(client):
    r = client.get("/users/1/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == 1
    assert body["n_transactions"] > 0


def test_batch_ok(client):
    r = client.post("/transactions/batch", json={"transactions": [_valid_txn()]})
    assert r.status_code == 200
    body = r.json()
    assert body["received"] == 1
    assert body["inserted"] == 1


# ===========================================================================
# Tests API — /anomalies
# ===========================================================================

def test_anomalies_high_threshold_empty(client):
    r = client.get("/anomalies?threshold=999999")
    assert r.status_code == 200
    body = r.json()
    assert body["anomalies"] == []
    assert body["threshold"] == 999999


def test_anomalies_threshold_zero_returns_users(client):
    r = client.get("/anomalies?threshold=0&window_days=365")
    assert r.status_code == 200
    body = r.json()
    assert len(body["anomalies"]) > 0
    counts = [a["failed_count"] for a in body["anomalies"]]
    assert counts == sorted(counts, reverse=True)


def test_anomalies_invalid_threshold_422(client):
    r = client.get("/anomalies?threshold=-1")
    assert r.status_code == 422


def test_anomalies_cache_hit(client):
    client.app.state.cache.clear()
    r1 = client.get("/anomalies?threshold=10&window_days=365")
    r2 = client.get("/anomalies?threshold=10&window_days=365")
    assert r1.json()["cached"] is False
    assert r2.json()["cached"] is True


# ===========================================================================
# Tests API — errores
# ===========================================================================

def test_user_not_found_stats(client):
    assert client.get("/users/999999/stats").status_code == 404


def test_user_not_found_transactions(client):
    assert client.get("/users/999999/transactions").status_code == 404


def test_batch_invalid_category_422(client):
    bad = _valid_txn(category="Groceries")
    assert client.post("/transactions/batch", json={"transactions": [bad]}).status_code == 422


def test_batch_invalid_amount_422(client):
    bad = _valid_txn(amount=99_999.0)
    assert client.post("/transactions/batch", json={"transactions": [bad]}).status_code == 422


def test_batch_dedupe(client):
    same_id = str(uuid.uuid4())
    a, b = _valid_txn(transaction_id=same_id), _valid_txn(transaction_id=same_id)
    r = client.post("/transactions/batch", json={"transactions": [a, b]})
    assert r.status_code == 200
    body = r.json()
    assert body["inserted"] == 1
    assert body["duplicates_skipped"] == 1
    r2 = client.post("/transactions/batch", json={"transactions": [_valid_txn(transaction_id=same_id)]})
    assert r2.json()["inserted"] == 0


def test_pagination_invalid_422(client):
    assert client.get("/users/1/transactions?page=0").status_code == 422
    assert client.get("/users/1/transactions?page_size=0").status_code == 422


def test_health_sla_p95_under_50ms(client):
    latencies = []
    for _ in range(50):
        t0 = time.perf_counter()
        r = client.get("/health")
        latencies.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    p95 = statistics.quantiles(latencies, n=20)[18]
    assert p95 < 50, f"p95 /health = {p95:.2f}ms excede 50ms SLA"


# ===========================================================================
# Fixtures pipeline
# ===========================================================================

def _make_csv(tmp_path: Path, rows: list[dict]) -> Path:
    if not rows:
        return tmp_path / "empty.csv"
    csv_path = tmp_path / "input.csv"
    with csv_path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    return csv_path


def _valid_row(**overrides) -> dict:
    row = {
        "transaction_id": str(uuid.uuid4()),
        "timestamp": "2025-06-01T10:00:00",
        "user_id": "1",
        "merchant_id": "100",
        "amount": "50.00",
        "category": "Food",
        "country_code": "MX",
        "status": "completed",
    }
    row.update(overrides)
    return row


# ===========================================================================
# Tests pipeline — extract
# ===========================================================================

def test_extract_normalizes_types():
    raw = _valid_row(user_id="42", merchant_id="7", amount="123.456", country_code="mx")
    rec = extract_record(raw)
    assert rec["user_id"] == 42
    assert rec["merchant_id"] == 7
    assert rec["amount"] == 123.46
    assert rec["country_code"] == "MX"


def test_extract_normalizes_timestamp_formats():
    formats = [
        "2025-06-01 10:00:00",
        "2025-06-01T10:00:00",
        "2025-06-01T10:00:00.000000",
    ]
    for fmt in formats:
        rec = extract_record(_valid_row(timestamp=fmt))
        assert "T" in rec["timestamp"]


def test_extract_passes_through_bad_values():
    rec = extract_record(_valid_row(amount="not_a_number", user_id="abc"))
    assert rec["amount"] == "not_a_number"
    assert rec["user_id"] == "abc"


def test_read_csv(tmp_path):
    rows = [_valid_row() for _ in range(5)]
    csv_path = _make_csv(tmp_path, rows)
    loaded = read_csv(csv_path)
    assert len(loaded) == 5
    assert "transaction_id" in loaded[0]


# ===========================================================================
# Tests pipeline — transform
# ===========================================================================

def _extracted_row(**overrides) -> dict:
    row = {
        "transaction_id": str(uuid.uuid4()),
        "timestamp": "2025-06-01T10:00:00",
        "user_id": 1,
        "merchant_id": 100,
        "amount": 50.0,
        "category": "Food",
        "country_code": "MX",
        "status": "completed",
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("field,bad_value,expected_reason", [
    ("amount", 9999.99, "amount_out_of_range"),
    ("category", "Groceries", "invalid_category"),
    ("country_code", "US", "invalid_country"),
    ("status", "cancelled", "invalid_status"),
    ("user_id", 99999, "invalid_user_id"),
    ("merchant_id", 99999, "invalid_merchant_id"),
    ("transaction_id", "not-a-uuid", "invalid_uuid"),
])
def test_transform_rejects_bad_values(tmp_path, field, bad_value, expected_reason):
    records = [_extracted_row(**{field: bad_value})]
    valid, rejected = transform(records, quarantine_dir=tmp_path / "q")
    assert valid == []
    assert rejected.get(expected_reason, 0) == 1


def test_transform_rejects_future_timestamp(tmp_path):
    future = (datetime.now() + timedelta(hours=2)).isoformat()
    records = [_extracted_row(timestamp=future)]
    valid, rejected = transform(records, quarantine_dir=tmp_path / "q")
    assert valid == []
    assert "future_timestamp" in rejected


def test_transform_rejects_null_field(tmp_path):
    records = [_extracted_row(amount=None)]
    valid, rejected = transform(records, quarantine_dir=tmp_path / "q")
    assert valid == []
    assert "null_field" in rejected


def test_transform_valid_passes(tmp_path):
    records = [_extracted_row() for _ in range(10)]
    valid, rejected = transform(records, quarantine_dir=tmp_path / "q")
    assert len(valid) == 10
    assert rejected == {}


# ===========================================================================
# Tests pipeline — load
# ===========================================================================

def test_load_inserts_and_dedupes(tmp_path):
    db = tmp_path / "test.db"
    rows = [_extracted_row() for _ in range(5)]
    r1 = load(rows, db)
    assert r1["inserted"] == 5
    assert r1["duplicates_skipped"] == 0
    r2 = load(rows, db)
    assert r2["inserted"] == 0
    assert r2["duplicates_skipped"] == 5


# ===========================================================================
# Tests pipeline — end-to-end
# ===========================================================================

def test_pipeline_e2e(tmp_path):
    rows = [_valid_row() for _ in range(20)]
    csv_path = _make_csv(tmp_path, rows)
    report = pipeline_run(
        csv_path=csv_path,
        db_path=tmp_path / "txns.db",
        quarantine_dir=tmp_path / "q",
        results_dir=tmp_path / "r",
    )
    assert report["extracted"] == 20
    assert report["valid"] == 20
    assert report["rejected"]["total"] == 0
    assert report["loaded"]["inserted"] == 20
    assert "report_file" in report


def test_pipeline_idempotent(tmp_path):
    rows = [_valid_row() for _ in range(10)]
    csv_path = _make_csv(tmp_path, rows)
    db = tmp_path / "txns.db"
    q = tmp_path / "q"
    r1 = pipeline_run(csv_path=csv_path, db_path=db, quarantine_dir=q, results_dir=tmp_path / "r1")
    r2 = pipeline_run(csv_path=csv_path, db_path=db, quarantine_dir=q, results_dir=tmp_path / "r2")
    assert r1["loaded"]["inserted"] == 10
    assert r2["loaded"]["inserted"] == 0
    assert r2["loaded"]["duplicates_skipped"] == 10


def test_pipeline_invalid_rows_quarantined(tmp_path):
    rows = [
        _valid_row(),
        _valid_row(category="Groceries"),
        _valid_row(amount="99999"),
        _valid_row(),
    ]
    csv_path = _make_csv(tmp_path, rows)
    report = pipeline_run(
        csv_path=csv_path,
        db_path=tmp_path / "txns.db",
        quarantine_dir=tmp_path / "q",
        results_dir=tmp_path / "r",
    )
    assert report["valid"] == 2
    assert report["rejected"]["total"] == 2
    assert report["loaded"]["inserted"] == 2


def test_pipeline_report_structure(tmp_path):
    csv_path = _make_csv(tmp_path, [_valid_row()])
    report = pipeline_run(
        csv_path=csv_path,
        db_path=tmp_path / "txns.db",
        quarantine_dir=tmp_path / "q",
        results_dir=tmp_path / "r",
    )
    for key in ("run_id", "started_at", "total_seconds", "source",
                "extracted", "valid", "rejected", "loaded", "db",
                "quarantine_file", "report_file"):
        assert key in report, f"Falta clave '{key}' en el reporte"
