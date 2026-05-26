"""Vistas DRF — los 6 endpoints del E4, ahora con Django REST Framework.

Reparto de backend (igual que E4, justificado en su architecture_decision.md):
  * /analytics/*  -> DuckDB sobre el Parquet (escaneos columnares full-table).
  * /users/*      -> ORM de Django (lookups por user_id sobre los indices del E3).
  * /transactions/batch -> ORM (escritura con dedupe por PK).
  * /health       -> sin backend pesado.

Autenticacion por token (TokenAuthentication):
  publicos       -> /health, /analytics/*
  requieren token-> /users/*, POST /transactions/batch
"""

from __future__ import annotations

import time

from django.db import transaction as db_transaction
from django.db.models import Count, Sum
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from . import analytics
from .models import Transaction
from .serializers import (
    BatchRequestSerializer,
    BatchResultSerializer,
    SummaryResponseSerializer,
    TopMerchantsResponseSerializer,
    TransactionOutputSerializer,
    UserStatsResponseSerializer,
    UserTransactionsResponseSerializer,
)

STARTED_AT = time.monotonic()


def _positive_int(raw: str | None, default: int, *, name: str, lo: int, hi: int) -> int:
    """Parsea un query param entero con rango, levantando 422 si es invalido."""
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValidationError({name: "debe ser un entero"})
    if value < lo or value > hi:
        raise ValidationError({name: f"debe estar entre {lo} y {hi}"})
    return value


# ---------------------------------------------------------------------------
# Analytics — DuckDB, publico
# ---------------------------------------------------------------------------

class AnalyticsViewSet(viewsets.ViewSet):
    permission_classes = [AllowAny]

    def list(self, request):  # GET /analytics -> indice de acciones
        return Response({"endpoints": ["summary", "top-merchants"]})

    # GET /analytics/summary
    def summary(self, request):
        data = analytics.summary()
        return Response(SummaryResponseSerializer(data).data)

    # GET /analytics/top-merchants?limit=N&country=XX
    def top_merchants(self, request):
        limit = _positive_int(
            request.query_params.get("limit"), 10, name="limit", lo=1, hi=100
        )
        country = request.query_params.get("country")
        if country is not None and len(country) != 2:
            raise ValidationError({"country": "debe ser un codigo de 2 letras"})
        merchants = analytics.top_merchants(limit, country)
        payload = {"limit": limit, "country": country, "merchants": merchants}
        return Response(TopMerchantsResponseSerializer(payload).data)


# ---------------------------------------------------------------------------
# Usuarios — ORM, requiere token
# ---------------------------------------------------------------------------

class UserViewSet(viewsets.ViewSet):
    """Accesos por usuario sobre el ORM (indices del E3)."""

    # GET /users/{pk}/transactions?page=N&page_size=M
    def transactions(self, request, pk=None):
        user_id = _positive_int(pk, 0, name="user_id", lo=1, hi=2_000_000_000)
        page = _positive_int(
            request.query_params.get("page"), 1, name="page", lo=1, hi=2_000_000_000
        )
        page_size = _positive_int(
            request.query_params.get("page_size"), 20, name="page_size", lo=1, hi=100
        )
        if not Transaction.objects.filter(user_id=user_id).exists():
            return Response(
                {"detail": f"user_id {user_id} no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )
        offset = (page - 1) * page_size
        qs = (
            Transaction.objects.filter(user_id=user_id)
            .order_by("-timestamp")[offset : offset + page_size]
        )
        rows = TransactionOutputSerializer(qs, many=True).data
        payload = {
            "user_id": user_id,
            "page": page,
            "page_size": page_size,
            "n_returned": len(rows),
            "transactions": rows,
        }
        return Response(UserTransactionsResponseSerializer(payload).data)

    # GET /users/{pk}/stats
    def stats(self, request, pk=None):
        user_id = _positive_int(pk, 0, name="user_id", lo=1, hi=2_000_000_000)
        qs = Transaction.objects.filter(user_id=user_id)
        agg = qs.aggregate(n=Count("transaction_id"), total=Sum("amount"))
        if not agg["n"]:
            return Response(
                {"detail": f"user_id {user_id} no encontrado"},
                status=status.HTTP_404_NOT_FOUND,
            )
        top_cat = (
            qs.values("category")
            .annotate(c=Count("transaction_id"))
            .order_by("-c", "category")
            .first()
        )
        top_country = (
            qs.values("country_code")
            .annotate(c=Count("transaction_id"))
            .order_by("-c", "country_code")
            .first()
        )
        payload = {
            "user_id": user_id,
            "n_transactions": int(agg["n"]),
            "total_amount": round(float(agg["total"] or 0.0), 2),
            "most_frequent_category": top_cat["category"],
            "country_code": top_country["country_code"],
        }
        return Response(UserStatsResponseSerializer(payload).data)


# ---------------------------------------------------------------------------
# Batch insert — ORM, requiere token
# ---------------------------------------------------------------------------

class TransactionViewSet(viewsets.ViewSet):

    # POST /transactions/batch
    def batch(self, request):
        serializer = BatchRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)  # -> 422 via exception handler
        items = serializer.validated_data["transactions"]

        # 1. Dedupe intra-lote (conserva la primera ocurrencia).
        seen: dict[str, dict] = {}
        intra_dups: list[str] = []
        for item in items:
            tid = item["transaction_id"]
            if tid in seen:
                intra_dups.append(tid)
            else:
                seen[tid] = item

        ids = list(seen.keys())
        # 2. Detectar cuales ya existen en la base.
        existing = set(
            Transaction.objects.filter(transaction_id__in=ids).values_list(
                "transaction_id", flat=True
            )
        )
        to_create = [
            Transaction(**item) for tid, item in seen.items() if tid not in existing
        ]
        # 3. Insertar en una sola transaccion atomica.
        if to_create:
            with db_transaction.atomic():
                Transaction.objects.bulk_create(to_create)

        payload = {
            "received": len(items),
            "inserted": len(to_create),
            "duplicates_skipped": len(intra_dups) + len(existing),
            "duplicate_ids": intra_dups + sorted(existing),
        }
        return Response(
            BatchResultSerializer(payload).data, status=status.HTTP_201_CREATED
        )


# ---------------------------------------------------------------------------
# Health — publico, sin auth
# ---------------------------------------------------------------------------

class HealthView(APIView):
    authentication_classes: list = []
    permission_classes = [AllowAny]

    def get(self, request):
        try:
            n = Transaction.objects.count()
            orm_ok = True
        except Exception:
            n = 0
            orm_ok = False
        return Response(
            {
                "status": "ok",
                "uptime_seconds": round(time.monotonic() - STARTED_AT, 3),
                "connections": {
                    "orm_sqlite": orm_ok,
                    "duckdb_parquet": analytics.healthy(),
                },
                "n_transactions": n,
            }
        )
