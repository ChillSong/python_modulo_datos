"""Suite de tests de la API del E4.

Cubre, segun la rubrica del PDF:
  * happy path de cada uno de los 6 endpoints,
  * usuario inexistente -> 404,
  * batch con schema invalido -> 422,
  * paginacion fuera de rango,
  * deduplicacion en el batch,
  * un test que valida que un SLA de latencia se cumple.

La base la construye setup_db.py.  Si no existe, los tests se saltan con un
mensaje claro en vez de fallar de forma confusa.

Nota de arquitectura: /analytics/* lee el Parquet via DuckDB (snapshot),
asi que los inserts del batch (que van a SQLite) NO alteran los conteos de
analytics — eso hace que los tests sean estables aunque inserten filas.
"""

from __future__ import annotations

import statistics
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import DEFAULT_DB
from app.main import app


@pytest.fixture(scope="module")
def client():
    if not DEFAULT_DB.exists():
        pytest.skip(
            "Falta la base. Corre: uv run python ejercicio-04-sistema/setup_db.py"
        )
    with TestClient(app) as c:
        yield c


def _valid_txn(**overrides) -> dict:
    txn = {
        "transaction_id": str(uuid.uuid4()),
        "timestamp": "2026-01-15T10:30:00",
        "user_id": 123,
        "merchant_id": 456,
        "amount": 99.95,
        "category": "Food",
        "country_code": "MX",
        "status": "completed",
    }
    txn.update(overrides)
    return txn


# ---------------------------------------------------------------------------
# Happy path de cada endpoint
# ---------------------------------------------------------------------------

def test_health_happy(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["connections"] == {"duckdb": True, "sqlite": True}
    assert body["uptime_seconds"] >= 0


def test_summary_happy(client):
    r = client.get("/analytics/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["n_transactions"] == 1_000_000
    assert len(body["by_country"]) == 15
    assert len(body["by_category"]) == 10
    assert body["total_amount"] > 0


def test_top_merchants_happy(client):
    r = client.get("/analytics/top-merchants?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 5
    assert len(body["merchants"]) == 5
    totals = [m["total_amount"] for m in body["merchants"]]
    assert totals == sorted(totals, reverse=True)  # orden descendente por volumen


def test_top_merchants_country_filter(client):
    r = client.get("/analytics/top-merchants?limit=3&country=MX")
    assert r.status_code == 200
    assert r.json()["country"] == "MX"
    assert len(r.json()["merchants"]) == 3


def test_user_transactions_happy(client):
    r = client.get("/users/1/transactions?page=1&page_size=10")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == 1
    assert body["n_returned"] <= 10
    # Ordenado por timestamp DESC.
    ts = [t["timestamp"] for t in body["transactions"]]
    assert ts == sorted(ts, reverse=True)


def test_user_stats_happy(client):
    r = client.get("/users/1/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == 1
    assert body["n_transactions"] > 0
    assert body["most_frequent_category"]
    assert len(body["country_code"]) == 2


def test_batch_happy(client):
    payload = {"transactions": [_valid_txn(), _valid_txn()]}
    r = client.post("/transactions/batch", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["received"] == 2
    assert body["inserted"] == 2
    assert body["duplicates_skipped"] == 0


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def test_summary_cache_hit(client):
    # Dos llamadas seguidas: la segunda debe venir del cache.
    first = client.get("/analytics/summary").json()
    second = client.get("/analytics/summary").json()
    assert first["cached"] is False or second["cached"] is True
    assert second["cached"] is True
    # El hit rate reportado por /health debe ser > 0 tras el hit.
    assert client.get("/health").json()["cache_hits"] >= 1


# ---------------------------------------------------------------------------
# Errores: 404 usuario inexistente
# ---------------------------------------------------------------------------

def test_user_stats_not_found(client):
    r = client.get("/users/999999/stats")
    assert r.status_code == 404


def test_user_transactions_not_found(client):
    r = client.get("/users/999999/transactions")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Errores: 422 batch con schema invalido
# ---------------------------------------------------------------------------

def test_batch_invalid_category(client):
    bad = _valid_txn(category="Groceries")  # no esta en el dominio fijo
    r = client.post("/transactions/batch", json={"transactions": [bad]})
    assert r.status_code == 422


def test_batch_invalid_amount(client):
    bad = _valid_txn(amount=999_999.0)  # fuera de 0.01..5000
    r = client.post("/transactions/batch", json={"transactions": [bad]})
    assert r.status_code == 422


def test_batch_too_many(client):
    payload = {"transactions": [_valid_txn() for _ in range(501)]}
    r = client.post("/transactions/batch", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Deduplicacion en el batch
# ---------------------------------------------------------------------------

def test_batch_dedupe(client):
    same_id = str(uuid.uuid4())
    a = _valid_txn(transaction_id=same_id)
    b = _valid_txn(transaction_id=same_id)  # duplicado intra-lote
    r = client.post("/transactions/batch", json={"transactions": [a, b]})
    assert r.status_code == 200
    body = r.json()
    assert body["inserted"] == 1
    assert body["duplicates_skipped"] == 1
    # Reenviar el mismo id: ahora ya existe en la base -> duplicado.
    r2 = client.post("/transactions/batch", json={"transactions": [_valid_txn(transaction_id=same_id)]})
    assert r2.json()["inserted"] == 0
    assert r2.json()["duplicates_skipped"] == 1


# ---------------------------------------------------------------------------
# Paginacion fuera de rango
# ---------------------------------------------------------------------------

def test_pagination_invalid_params_422(client):
    assert client.get("/users/1/transactions?page=0").status_code == 422
    assert client.get("/users/1/transactions?page_size=0").status_code == 422
    assert client.get("/users/1/transactions?page_size=1000").status_code == 422


def test_pagination_beyond_data_empty(client):
    # Pagina valida pero mas alla de los datos del usuario: 200 con lista vacia.
    r = client.get("/users/1/transactions?page=99999&page_size=50")
    assert r.status_code == 200
    assert r.json()["n_returned"] == 0


# ---------------------------------------------------------------------------
# SLA de latencia: /health debe responder < 50ms (su SLA "siempre")
# ---------------------------------------------------------------------------

def test_health_sla_latency(client):
    latencies = []
    for _ in range(50):
        t0 = time.perf_counter()
        r = client.get("/health")
        latencies.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200
    p95 = statistics.quantiles(latencies, n=20)[18]  # percentil 95
    assert p95 < 50, f"p95 /health = {p95:.2f}ms excede el SLA de 50ms"


def test_summary_warm_faster_than_cold(client):
    # Cold: forzar recomputo limpiando el cache via primera llamada tras clear.
    client.app.state.cache.clear()
    t0 = time.perf_counter()
    client.get("/analytics/summary")  # cold (miss)
    cold = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    client.get("/analytics/summary")  # warm (hit)
    warm = (time.perf_counter() - t1) * 1000
    assert warm < cold, f"warm ({warm:.2f}ms) deberia ser < cold ({cold:.2f}ms)"
