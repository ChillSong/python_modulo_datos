"""Django settings — Ejercicio 5 (El Backend con Estructura).

Reconstruye los 6 endpoints del E4 con Django + DRF. Lo configurable (ruta de
la base, ruta del Parquet para analytics, SECRET_KEY, DEBUG) sale de variables
de entorno con defaults razonables para desarrollo.

Variables de entorno:
    E5_SECRET_KEY   SECRET_KEY de Django (default: clave de desarrollo)
    E5_DEBUG        "1"/"0" (default 1)
    E5_DB           ruta del SQLite gestionado por el ORM (default db.sqlite3)
    E5_PARQUET      Parquet de 1M filas del E1 que DuckDB usa en /analytics/*
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent  # python_modulo_datos/

SECRET_KEY = os.environ.get(
    "E5_SECRET_KEY",
    "django-insecure-epi+hizt(4^d#5vc7w4$57bc=9ckra(l5x%d0twbm3l1%rnfi0",
)
DEBUG = os.environ.get("E5_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework.authtoken",
    "transactions",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("E5_DB", str(BASE_DIR / "db.sqlite3")),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
# Timestamps del dataset son naive (igual que E1/E3/E4). Sin tz para guardarlos
# tal cual y evitar warnings de naive datetime en la ingesta.
USE_TZ = False

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Parquet de 1M filas del E1 — DuckDB lo lee directo en /analytics/* (igual que E4).
E5_PARQUET = os.environ.get(
    "E5_PARQUET",
    str(REPO_ROOT / "data" / "benchmark_1m" / "transactions.snappy.parquet"),
)

# --- Django REST Framework -------------------------------------------------
# Auth por token. Por defecto TODO requiere autenticacion; las vistas publicas
# (/health, /analytics/*) bajan el permiso a AllowAny explicitamente.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    # Mapea los errores de validacion de DRF (400 por defecto) a 422, igual que
    # FastAPI/Pydantic en el E4. Lo exige la rubrica ("422 en batch invalido").
    "EXCEPTION_HANDLER": "transactions.exceptions.validation_to_422_handler",
}
