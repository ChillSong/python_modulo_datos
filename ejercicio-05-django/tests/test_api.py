"""Tests de la API (E5, Django + DRF).

Cubre lo que pide la rubrica: happy path por endpoint, 401 sin token en los
endpoints protegidos, y 422 en batch invalido. Ademas: dedupe, paginacion y
validacion de rangos.

Convencion: `/analytics/*` lee el Parquet real del E1 (1M filas); `/users/*` y
el batch usan la base de prueba del ORM (filas de `sample_user`).
"""

from __future__ import annotations

import uuid

import pytest

from conftest import SAMPLE_USER_ID

pytestmark = pytest.mark.django_db


def _valid_txn(**overrides) -> dict:
    txn = {
        "transaction_id": str(uuid.uuid4()),
        "timestamp": "2026-02-01T10:30:00",
        "user_id": 42,
        "merchant_id": 7,
        "amount": 99.95,
        "category": "Food",
        "country_code": "MX",
        "status": "completed",
    }
    txn.update(overrides)
    return txn


# --- Health (publico) ------------------------------------------------------

def test_health_public_ok(api_client):
    r = api_client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "uptime_seconds" in body
    assert body["connections"]["orm_sqlite"] is True


# --- Analytics (publico, DuckDB sobre Parquet) -----------------------------

def test_analytics_summary_public(api_client):
    r = api_client.get("/analytics/summary")
    assert r.status_code == 200
    body = r.json()
    assert body["n_transactions"] == 1_000_000
    assert len(body["by_country"]) == 15
    assert len(body["by_category"]) == 10


def test_analytics_top_merchants_public(api_client):
    r = api_client.get("/analytics/top-merchants?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["limit"] == 5
    assert len(body["merchants"]) == 5
    # Ordenado por total descendente.
    totals = [m["total_amount"] for m in body["merchants"]]
    assert totals == sorted(totals, reverse=True)


def test_analytics_top_merchants_invalid_limit_422(api_client):
    r = api_client.get("/analytics/top-merchants?limit=999")
    assert r.status_code == 422


# --- Usuarios (requieren token) --------------------------------------------

def test_users_transactions_requires_token(api_client, sample_user):
    r = api_client.get(f"/users/{sample_user}/transactions")
    assert r.status_code == 401


def test_users_transactions_happy(auth_client, sample_user):
    r = auth_client.get(f"/users/{sample_user}/transactions")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == sample_user
    assert body["n_returned"] == 3
    # Orden por timestamp DESC: la mas reciente primero.
    assert body["transactions"][0]["transaction_id"] == "sample-2"


def test_users_transactions_pagination(auth_client, sample_user):
    r = auth_client.get(f"/users/{sample_user}/transactions?page=2&page_size=2")
    assert r.status_code == 200
    body = r.json()
    assert body["page"] == 2
    assert body["n_returned"] == 1  # 3 filas, pagina 2 con tamano 2 -> 1


def test_users_transactions_pagination_invalid_422(auth_client, sample_user):
    r = auth_client.get(f"/users/{sample_user}/transactions?page=0")
    assert r.status_code == 422


def test_users_stats_happy(auth_client, sample_user):
    r = auth_client.get(f"/users/{sample_user}/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == sample_user
    assert body["n_transactions"] == 3
    assert body["total_amount"] == 60.0  # 10 + 20 + 30
    assert body["most_frequent_category"] == "Food"  # 2 de 3
    assert body["country_code"] == "MX"


def test_user_not_found_404(auth_client):
    r = auth_client.get("/users/999999/stats")
    assert r.status_code == 404


# --- Batch insert (requiere token) -----------------------------------------

def test_batch_requires_token(api_client):
    r = api_client.post(
        "/transactions/batch", {"transactions": [_valid_txn()]}, format="json"
    )
    assert r.status_code == 401


def test_batch_happy(auth_client):
    payload = {"transactions": [_valid_txn(), _valid_txn()]}
    r = auth_client.post("/transactions/batch", payload, format="json")
    assert r.status_code == 201
    body = r.json()
    assert body["received"] == 2
    assert body["inserted"] == 2
    assert body["duplicates_skipped"] == 0


def test_batch_dedupe_intra_lote(auth_client):
    txn = _valid_txn()
    payload = {"transactions": [txn, dict(txn)]}  # mismo transaction_id dos veces
    r = auth_client.post("/transactions/batch", payload, format="json")
    assert r.status_code == 201
    body = r.json()
    assert body["inserted"] == 1
    assert body["duplicates_skipped"] == 1
    assert txn["transaction_id"] in body["duplicate_ids"]


def test_batch_dedupe_contra_base(auth_client):
    txn = _valid_txn()
    auth_client.post("/transactions/batch", {"transactions": [txn]}, format="json")
    # Reenviar el mismo: ya existe en la base.
    r = auth_client.post("/transactions/batch", {"transactions": [txn]}, format="json")
    body = r.json()
    assert body["inserted"] == 0
    assert body["duplicates_skipped"] == 1


def test_batch_invalid_category_422(auth_client):
    bad = _valid_txn(category="Groceries")  # no es una categoria valida
    r = auth_client.post("/transactions/batch", {"transactions": [bad]}, format="json")
    assert r.status_code == 422


def test_batch_invalid_amount_422(auth_client):
    bad = _valid_txn(amount=999999.0)  # fuera de rango
    r = auth_client.post("/transactions/batch", {"transactions": [bad]}, format="json")
    assert r.status_code == 422


def test_batch_extra_field_422(auth_client):
    bad = _valid_txn(extra_field="no permitido")
    r = auth_client.post("/transactions/batch", {"transactions": [bad]}, format="json")
    assert r.status_code == 422


def test_batch_too_large_422(auth_client):
    payload = {"transactions": [_valid_txn() for _ in range(501)]}
    r = auth_client.post("/transactions/batch", payload, format="json")
    assert r.status_code == 422
