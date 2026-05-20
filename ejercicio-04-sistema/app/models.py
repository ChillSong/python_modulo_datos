"""Modelos Pydantic para validacion de entrada y forma de las respuestas.

El schema del dataset es FIJO en todo el modulo (ver CLAUDE.md / E1).  El
modelo `TransactionIn` reproduce ese contrato con restricciones explicitas:
si el cliente manda algo fuera de rango o una categoria/pais/status invalido,
FastAPI responde HTTP 422 con el detalle del campo — sin tocar la base.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Dominios fijos del schema (E1 paso 1).  Como Literal para que un valor
# fuera del conjunto produzca un 422 con mensaje claro, no un insert silencioso.
Category = Literal[
    "Food", "Travel", "Electronics", "Health", "Entertainment",
    "Retail", "Transport", "Education", "Services", "Other",
]
CountryCode = Literal[
    "MX", "CO", "BR", "AR", "CL", "PE", "EC", "VE",
    "BO", "PY", "UY", "CR", "GT", "PA", "DO",
]
Status = Literal["completed", "failed", "pending"]


# ---------------------------------------------------------------------------
# Entrada — POST /transactions/batch
# ---------------------------------------------------------------------------

class TransactionIn(BaseModel):
    """Una transaccion candidata a insertarse.  Validacion estricta."""
    model_config = {"extra": "forbid"}  # rechaza campos no declarados

    transaction_id: str = Field(min_length=1, max_length=64)
    timestamp: datetime
    user_id: int = Field(ge=1, le=50_000)
    merchant_id: int = Field(ge=1, le=10_000)
    amount: float = Field(ge=0.01, le=5_000.00)
    category: Category
    country_code: CountryCode
    status: Status


class BatchRequest(BaseModel):
    """Hasta 500 transacciones por llamada (limite del PDF)."""
    transactions: list[TransactionIn] = Field(min_length=1, max_length=500)


class BatchResult(BaseModel):
    received: int
    inserted: int
    duplicates_skipped: int
    duplicate_ids: list[str]


# ---------------------------------------------------------------------------
# Respuestas — analytics
# ---------------------------------------------------------------------------

class CountryCount(BaseModel):
    country_code: str
    n_transactions: int
    total_amount: float


class CategoryCount(BaseModel):
    category: str
    n_transactions: int
    total_amount: float


class SummaryResponse(BaseModel):
    n_transactions: int
    total_amount: float
    avg_amount: float
    by_country: list[CountryCount]
    by_category: list[CategoryCount]
    cached: bool = False


class Merchant(BaseModel):
    merchant_id: int
    n_transactions: int
    total_amount: float


class TopMerchantsResponse(BaseModel):
    limit: int
    country: str | None
    merchants: list[Merchant]
    cached: bool = False


# ---------------------------------------------------------------------------
# Respuestas — transaccional (SQLite)
# ---------------------------------------------------------------------------

class TransactionOut(BaseModel):
    transaction_id: str
    timestamp: str
    user_id: int
    merchant_id: int
    amount: float
    category: str
    country_code: str
    status: str


class UserTransactionsResponse(BaseModel):
    user_id: int
    page: int
    page_size: int
    n_returned: int
    transactions: list[TransactionOut]


class UserStatsResponse(BaseModel):
    user_id: int
    n_transactions: int
    total_amount: float
    most_frequent_category: str
    country_code: str


# ---------------------------------------------------------------------------
# Respuesta — /health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    connections: dict[str, bool]
    cache_hits: int
    cache_misses: int
    cache_hit_rate: float
    cache_entries: int
