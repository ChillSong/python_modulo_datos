"""URLs del proyecto.

Rutas:
    /admin/            -> Django Admin
    /api-token-auth/   -> POST {username, password} devuelve {"token": ...}
    /...               -> los 6 endpoints (ver transactions/urls.py)
"""

from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api-token-auth/", obtain_auth_token),
    path("", include("transactions.urls")),
]
