"""API FastAPI — E8 Proyecto Final.

7 endpoints sobre arquitectura dual DuckDB + SQLite.
Nuevo respecto al E4: GET /anomalies?threshold=N&window_days=30

Variables de entorno:
    PARQUET_PATH            ruta al Parquet del E1
    DB_PATH                 ruta al SQLite
    CACHE_TTL_SUMMARY       TTL cache summary (default 30s)
    CACHE_TTL_TOP_MERCHANTS TTL cache top-merchants (default 30s)
    CACHE_TTL_ANOMALIES     TTL cache anomalies (default 30s)
"""

from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from . import db
from .cache import TTLCache
from .models import (
    AnomalyResponse,
    BatchRequest,
    BatchResult,
    HealthResponse,
    SummaryResponse,
    TopMerchantsResponse,
    UserStatsResponse,
    UserTransactionsResponse,
)

TTL_SUMMARY = float(os.environ.get("CACHE_TTL_SUMMARY", "30"))
TTL_TOP_MERCHANTS = float(os.environ.get("CACHE_TTL_TOP_MERCHANTS", "30"))
TTL_ANOMALIES = float(os.environ.get("CACHE_TTL_ANOMALIES", "30"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    parquet = Path(os.environ["PARQUET_PATH"]) if "PARQUET_PATH" in os.environ else None
    db_path = Path(os.environ["DB_PATH"]) if "DB_PATH" in os.environ else None
    app.state.backends = db.init_backends(parquet, db_path)
    app.state.cache = TTLCache()
    app.state.started_at = time.monotonic()
    try:
        yield
    finally:
        app.state.backends.close()


app = FastAPI(title="Fintech LATAM — Monitoreo de Transacciones (E8)", lifespan=lifespan)


def get_backends(request: Request) -> db.Backends:
    return request.app.state.backends


def get_cache(request: Request) -> TTLCache:
    return request.app.state.cache


# ---------------------------------------------------------------------------
# Analytics — DuckDB, con cache
# ---------------------------------------------------------------------------

@app.get("/analytics/summary", response_model=SummaryResponse)
def analytics_summary(
    backends: db.Backends = Depends(get_backends),
    cache: TTLCache = Depends(get_cache),
):
    key = "summary"
    hit, value = cache.get(key)
    if hit:
        return {**value, "cached": True}
    value = db.analytics_summary(backends)
    cache.set(key, value, TTL_SUMMARY)
    return {**value, "cached": False}


@app.get("/analytics/top-merchants", response_model=TopMerchantsResponse)
def top_merchants(
    limit: int = Query(10, ge=1, le=100),
    country: str | None = Query(None, min_length=2, max_length=2),
    backends: db.Backends = Depends(get_backends),
    cache: TTLCache = Depends(get_cache),
):
    key = f"top-merchants:{limit}:{country}"
    hit, value = cache.get(key)
    if hit:
        return {**value, "cached": True}
    merchants = db.top_merchants(backends, limit, country)
    value = {"limit": limit, "country": country, "merchants": merchants}
    cache.set(key, value, TTL_TOP_MERCHANTS)
    return {**value, "cached": False}


# ---------------------------------------------------------------------------
# Deteccion de anomalias — DuckDB, con cache  (requisito E8)
# ---------------------------------------------------------------------------

@app.get("/anomalies", response_model=AnomalyResponse)
def get_anomalies(
    threshold: int = Query(5, ge=0, description="Retorna usuarios con MAS de N transacciones fallidas"),
    window_days: int = Query(30, ge=1, le=365, description="Ventana de tiempo en dias"),
    backends: db.Backends = Depends(get_backends),
    cache: TTLCache = Depends(get_cache),
):
    key = f"anomalies:{threshold}:{window_days}"
    hit, value = cache.get(key)
    if hit:
        return {**value, "cached": True}
    anomalies = db.anomaly_detection(backends, threshold, window_days)
    value = {"threshold": threshold, "window_days": window_days, "anomalies": anomalies}
    cache.set(key, value, TTL_ANOMALIES)
    return {**value, "cached": False}


# ---------------------------------------------------------------------------
# Transaccional — SQLite, sin cache
# ---------------------------------------------------------------------------

@app.get("/users/{user_id}/transactions", response_model=UserTransactionsResponse)
def get_user_transactions(
    user_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    backends: db.Backends = Depends(get_backends),
):
    if not db.user_exists(backends, user_id):
        raise HTTPException(status_code=404, detail=f"user_id {user_id} no encontrado")
    rows = db.user_transactions(backends, user_id, page, page_size)
    return {
        "user_id": user_id,
        "page": page,
        "page_size": page_size,
        "n_returned": len(rows),
        "transactions": rows,
    }


@app.get("/users/{user_id}/stats", response_model=UserStatsResponse)
def get_user_stats(
    user_id: int,
    backends: db.Backends = Depends(get_backends),
):
    stats = db.user_stats(backends, user_id)
    if stats is None:
        raise HTTPException(status_code=404, detail=f"user_id {user_id} no encontrado")
    return stats


@app.post("/transactions/batch", response_model=BatchResult, status_code=200)
def post_batch(
    body: BatchRequest,
    backends: db.Backends = Depends(get_backends),
):
    inserted, duplicate_ids = db.insert_batch(backends, body.transactions)
    return {
        "received": len(body.transactions),
        "inserted": inserted,
        "duplicates_skipped": len(duplicate_ids),
        "duplicate_ids": duplicate_ids,
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health(request: Request):
    state = request.app.state
    stats = state.cache.stats
    return {
        "status": "ok",
        "uptime_seconds": round(time.monotonic() - state.started_at, 3),
        "connections": state.backends.healthy(),
        "cache_hits": stats["hits"],
        "cache_misses": stats["misses"],
        "cache_hit_rate": round(stats["hit_rate"], 4),
        "cache_entries": stats["entries"],
    }
