"""Serializers DRF — entrada y forma de las respuestas.

`TransactionInputSerializer` reproduce el contrato estricto del `TransactionIn`
de Pydantic del E4: rangos, dominios fijos (choices) y rechazo de campos no
declarados. Una entrada invalida produce un error de validacion que el handler
de `exceptions.py` convierte en HTTP 422.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import (
    CATEGORY_CHOICES,
    COUNTRY_CHOICES,
    STATUS_CHOICES,
    Transaction,
)

# Solo los valores (no los pares) para los ChoiceField.
_CATEGORIES = [c for c, _ in CATEGORY_CHOICES]
_COUNTRIES = [c for c, _ in COUNTRY_CHOICES]
_STATUSES = [s for s, _ in STATUS_CHOICES]


# ---------------------------------------------------------------------------
# Entrada — POST /transactions/batch
# ---------------------------------------------------------------------------

class TransactionInputSerializer(serializers.Serializer):
    """Una transaccion candidata. Validacion estricta (rangos + dominios)."""

    transaction_id = serializers.CharField(min_length=1, max_length=64)
    timestamp = serializers.DateTimeField()
    user_id = serializers.IntegerField(min_value=1, max_value=50_000)
    merchant_id = serializers.IntegerField(min_value=1, max_value=10_000)
    amount = serializers.FloatField(min_value=0.01, max_value=5_000.00)
    category = serializers.ChoiceField(choices=_CATEGORIES)
    country_code = serializers.ChoiceField(choices=_COUNTRIES)
    status = serializers.ChoiceField(choices=_STATUSES)

    def to_internal_value(self, data):
        # Rechaza campos no declarados (equivalente a extra="forbid" de Pydantic).
        # Se hace aqui (no en validate) porque como hijo de un ListField el
        # serializer no expone `initial_data`, pero si recibe el dict crudo.
        if isinstance(data, dict):
            extra = set(data) - set(self.fields)
            if extra:
                raise serializers.ValidationError(
                    {k: "campo no permitido" for k in sorted(extra)}
                )
        return super().to_internal_value(data)


class BatchRequestSerializer(serializers.Serializer):
    """Hasta 500 transacciones por llamada (limite del PDF)."""

    transactions = serializers.ListField(
        child=TransactionInputSerializer(),
        min_length=1,
        max_length=500,
    )


# ---------------------------------------------------------------------------
# Salida — transaccional
# ---------------------------------------------------------------------------

class TransactionOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = [
            "transaction_id", "timestamp", "user_id", "merchant_id",
            "amount", "category", "country_code", "status",
        ]


class UserTransactionsResponseSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    page = serializers.IntegerField()
    page_size = serializers.IntegerField()
    n_returned = serializers.IntegerField()
    transactions = TransactionOutputSerializer(many=True)


class UserStatsResponseSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    n_transactions = serializers.IntegerField()
    total_amount = serializers.FloatField()
    most_frequent_category = serializers.CharField()
    country_code = serializers.CharField()


class BatchResultSerializer(serializers.Serializer):
    received = serializers.IntegerField()
    inserted = serializers.IntegerField()
    duplicates_skipped = serializers.IntegerField()
    duplicate_ids = serializers.ListField(child=serializers.CharField())


# ---------------------------------------------------------------------------
# Salida — analytics
# ---------------------------------------------------------------------------

class CountryCountSerializer(serializers.Serializer):
    country_code = serializers.CharField()
    n_transactions = serializers.IntegerField()
    total_amount = serializers.FloatField()


class CategoryCountSerializer(serializers.Serializer):
    category = serializers.CharField()
    n_transactions = serializers.IntegerField()
    total_amount = serializers.FloatField()


class SummaryResponseSerializer(serializers.Serializer):
    n_transactions = serializers.IntegerField()
    total_amount = serializers.FloatField()
    avg_amount = serializers.FloatField()
    by_country = CountryCountSerializer(many=True)
    by_category = CategoryCountSerializer(many=True)


class MerchantSerializer(serializers.Serializer):
    merchant_id = serializers.IntegerField()
    n_transactions = serializers.IntegerField()
    total_amount = serializers.FloatField()


class TopMerchantsResponseSerializer(serializers.Serializer):
    limit = serializers.IntegerField()
    country = serializers.CharField(allow_null=True)
    merchants = MerchantSerializer(many=True)
