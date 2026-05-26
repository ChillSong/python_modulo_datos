"""Fixtures compartidas de los tests (pytest-django).

Los tests de `/analytics/*` corren contra el Parquet real del E1 (DuckDB),
independiente de la base de prueba. Los tests de `/users/*` y del batch usan la
base de prueba del ORM, donde insertamos filas conocidas en `sample_user`.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from django.contrib.auth.models import User
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from transactions.models import Transaction

SAMPLE_USER_ID = 12345


@pytest.fixture
def api_client() -> APIClient:
    """Cliente sin autenticar."""
    return APIClient()


@pytest.fixture
def auth_client(db) -> APIClient:
    """Cliente con token valido en el header Authorization."""
    user = User.objects.create_user(username="becario", password="x")
    token = Token.objects.create(user=user)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


@pytest.fixture
def sample_user(db):
    """Inserta 3 transacciones para SAMPLE_USER_ID en la base de prueba."""
    rows = [
        Transaction(
            transaction_id=f"sample-{i}",
            timestamp=datetime(2026, 1, 10 + i, 12, 0, 0),
            user_id=SAMPLE_USER_ID,
            merchant_id=100 + i,
            amount=10.0 * (i + 1),
            category="Food" if i < 2 else "Travel",
            country_code="MX",
            status="completed",
        )
        for i in range(3)
    ]
    Transaction.objects.bulk_create(rows)
    return SAMPLE_USER_ID
