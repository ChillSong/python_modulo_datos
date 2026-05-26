"""Rutas de los 6 endpoints.

Mapeo explicito de ViewSet -> metodo, sin trailing slash, para igualar las
rutas del E4. Permisos: /health y /analytics/* son publicos; /users/* y
/transactions/batch requieren token (ver permisos en views.py / settings.py).
"""

from django.urls import path

from .views import AnalyticsViewSet, HealthView, TransactionViewSet, UserViewSet

urlpatterns = [
    path("health", HealthView.as_view(), name="health"),
    path(
        "analytics/summary",
        AnalyticsViewSet.as_view({"get": "summary"}),
        name="analytics-summary",
    ),
    path(
        "analytics/top-merchants",
        AnalyticsViewSet.as_view({"get": "top_merchants"}),
        name="analytics-top-merchants",
    ),
    path(
        "users/<int:pk>/transactions",
        UserViewSet.as_view({"get": "transactions"}),
        name="user-transactions",
    ),
    path(
        "users/<int:pk>/stats",
        UserViewSet.as_view({"get": "stats"}),
        name="user-stats",
    ),
    path(
        "transactions/batch",
        TransactionViewSet.as_view({"post": "batch"}),
        name="transactions-batch",
    ),
]
