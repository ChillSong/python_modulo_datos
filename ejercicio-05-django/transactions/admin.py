"""Django Admin para Transaction.

list_display con las columnas mas utiles, filtros por status y country_code,
y busqueda por transaction_id y user_id (paso 5 del PDF).
"""

from django.contrib import admin

from .models import Transaction


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = (
        "transaction_id",
        "timestamp",
        "user_id",
        "merchant_id",
        "amount",
        "category",
        "country_code",
        "status",
    )
    list_filter = ("status", "country_code", "category")
    search_fields = ("transaction_id", "user_id")
    ordering = ("-timestamp",)
    list_per_page = 50
