"""Modelo Transaction — el schema FIJO del modulo, ahora gestionado por el ORM.

El schema es el mismo de E1/E3/E4 (ver CLAUDE.md). Los dos indices secundarios
se declaran en `Meta.indexes` para que la migracion los cree con los MISMOS
nombres y columnas que el E3 (`idx_txns_user_timestamp`, `idx_txns_country_user`).
La PK natural sobre `transaction_id` reproduce el indice implicito del E3.
"""

from __future__ import annotations

from django.db import models

# Dominios fijos del schema (E1 paso 1). Como `choices` para que el ORM y el
# Django Admin validen, y para que un valor fuera del conjunto sea rechazado.
CATEGORY_CHOICES = [
    (c, c)
    for c in (
        "Food", "Travel", "Electronics", "Health", "Entertainment",
        "Retail", "Transport", "Education", "Services", "Other",
    )
]
COUNTRY_CHOICES = [
    (c, c)
    for c in (
        "MX", "CO", "BR", "AR", "CL", "PE", "EC", "VE",
        "BO", "PY", "UY", "CR", "GT", "PA", "DO",
    )
]
STATUS_CHOICES = [(s, s) for s in ("completed", "failed", "pending")]


class Transaction(models.Model):
    # PK natural: reproduce el indice implicito del E3 sobre transaction_id (cubre P1).
    transaction_id = models.CharField(max_length=64, primary_key=True)
    timestamp = models.DateTimeField()
    user_id = models.IntegerField()
    merchant_id = models.IntegerField()
    amount = models.FloatField()
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    country_code = models.CharField(max_length=2, choices=COUNTRY_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)

    class Meta:
        db_table = "transactions"
        indexes = [
            # E3: composite leftmost-prefix. Sirve a P2/P3/P4 (user_id + ORDER BY ts DESC).
            models.Index(
                fields=["user_id", "-timestamp"], name="idx_txns_user_timestamp"
            ),
            # E3: P5 filtra por country_code y agrupa por user_id.
            models.Index(
                fields=["country_code", "user_id"], name="idx_txns_country_user"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.transaction_id} u{self.user_id} {self.amount} {self.status}"
