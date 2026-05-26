# Ejercicio 5 — El Backend con Estructura

Los **mismos 6 endpoints del E4**, reconstruidos con **Django + Django REST
Framework**. Los datos de usuario viven en una base gestionada por el **ORM de
Django** (no SQL crudo); los analíticos se sirven con **DuckDB** directo sobre
el Parquet del E1. Incluye autenticación por token y panel de administración.

## Arquitectura en una imagen

```
   HTTP ──► Django + DRF (config/urls.py → transactions/)
            │
            ├── /health                 público   ── ORM (count) + ping DuckDB
            ├── /analytics/summary      público   ── DuckDB  ─► data/.../*.parquet (E1)
            ├── /analytics/top-merchants público  ── DuckDB  ─┘
            ├── /users/{id}/transactions  token   ── ORM  ─► db.sqlite3 (índices E3)
            ├── /users/{id}/stats         token   ── ORM  ─┘
            └── POST /transactions/batch  token   ── ORM  (bulk_create + dedupe por PK)
```

| Pieza | Backend | Por qué |
|---|---|---|
| `/analytics/*` | DuckDB sobre Parquet | Agregados full-scan columnares. El PDF permite no usar el ORM aquí; mismo reparto OLAP/OLTP que el E4. |
| `/users/*`, batch | ORM de Django | Lookups por `user_id` y escrituras sobre los índices del E3, replicados con `Meta.indexes`. |

Los dos índices del E3 (`idx_txns_user_timestamp`, `idx_txns_country_user`) se
declaran en `transactions/models.py` (`Meta.indexes`) y la migración los crea con
los mismos nombres y columnas (verificable con `manage.py sqlmigrate transactions 0001`).

## Endpoints

| Método | Ruta | Auth | Descripción |
|---|---|:--:|---|
| GET | `/health` | pública | Estado del ORM y DuckDB, uptime, total de filas |
| GET | `/analytics/summary` | pública | Totales globales + breakdown por país y categoría |
| GET | `/analytics/top-merchants?limit=N&country=XX` | pública | Top N merchants por volumen |
| GET | `/users/{id}/transactions?page=N&page_size=M` | **token** | Transacciones del usuario, paginadas |
| GET | `/users/{id}/stats` | **token** | Total, conteo, categoría y país más frecuentes |
| POST | `/transactions/batch` | **token** | Hasta 500 transacciones, valida (422) y deduplica |

Autenticación: `TokenAuthentication` de DRF. Endpoints protegidos esperan el
header `Authorization: Token <token>`; sin él responden **401**. Una entrada
inválida en el batch responde **422** (paridad con el E4/Pydantic).

## Puesta en marcha (desde esta carpeta)

```bash
cd ejercicio-05-django

# 1. Aplicar migraciones (crea la tabla + los índices del E3)
uv run python manage.py migrate

# 2. Cargar el dataset del E1 en la base vía el ORM (idempotente, ~2 min para 1M)
uv run python manage.py load_transactions

# 3. Crear el superusuario (para el Django Admin)
uv run python manage.py createsuperuser

# 4. Generar un token de API para ese usuario
uv run python manage.py drf_create_token <usuario>

# 5. Levantar el servidor
uv run python manage.py runserver
```

La API queda en `http://127.0.0.1:8000`; el admin en `http://127.0.0.1:8000/admin/`.

> Si el Parquet de 1M filas no existe, genéralo primero con el E1:
> ```bash
> uv run python ejercicio-01-formatos/generate_data.py --size 1m
> uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m --formats parquet_snappy
> ```

### Obtener un token sin management command

Alternativa al paso 4, contra el servidor ya levantado:

```bash
curl -s -X POST http://127.0.0.1:8000/api-token-auth/ \
     -d 'username=<usuario>&password=<password>'
# -> {"token":"<token>"}
```

## Ejemplos de uso

```bash
TOKEN=<tu-token>
B=http://127.0.0.1:8000

# Públicos
curl "$B/health"
curl "$B/analytics/summary"
curl "$B/analytics/top-merchants?limit=5&country=MX"

# Protegidos (token)
curl -H "Authorization: Token $TOKEN" "$B/users/1/stats"
curl -H "Authorization: Token $TOKEN" "$B/users/1/transactions?page=1&page_size=20"
curl -X POST -H "Authorization: Token $TOKEN" -H 'Content-Type: application/json' \
     -d '{"transactions":[{"transaction_id":"demo-1","timestamp":"2026-01-15T10:30:00","user_id":1,"merchant_id":42,"amount":99.95,"category":"Food","country_code":"MX","status":"completed"}]}' \
     "$B/transactions/batch"
```

## Django Admin

Registrado en `transactions/admin.py`: `list_display` con todas las columnas,
filtros por `status` / `country_code` / `category`, y búsqueda por
`transaction_id` y `user_id`. Navegable en `/admin/` tras crear el superusuario.

## Tests

```bash
# Desde esta carpeta (ejercicio-05-django)
uv run pytest -q
```

18 tests (pytest-django): happy path por endpoint, **401 sin token**, **422 en
batch inválido**, dedupe (intra-lote y contra la base), paginación y validación
de rangos. Los tests de `/analytics/*` corren contra el Parquet real del E1; los
de `/users/*` y el batch usan la base de prueba del ORM.

## Variables de entorno

| Variable | Default | Para qué |
|---|---|---|
| `E5_SECRET_KEY` | clave de desarrollo | `SECRET_KEY` de Django |
| `E5_DEBUG` | `1` | `DEBUG` (`1`/`0`) |
| `E5_DB` | `ejercicio-05-django/db.sqlite3` | Base SQLite gestionada por el ORM |
| `E5_PARQUET` | `data/benchmark_1m/transactions.snappy.parquet` | Parquet que lee DuckDB en `/analytics/*` |

## Estructura

```
ejercicio-05-django/
├── manage.py
├── pytest.ini
├── conftest.py
├── config/                       # proyecto Django (settings, urls, wsgi/asgi)
├── transactions/
│   ├── models.py                 # Transaction (schema fijo + Meta.indexes del E3)
│   ├── serializers.py            # entrada estricta (422) + salida por endpoint
│   ├── views.py                  # 6 endpoints (ViewSets DRF) + permisos
│   ├── urls.py                   # rutas sin trailing slash (igual que E4)
│   ├── admin.py                  # Django Admin
│   ├── analytics.py              # DuckDB singleton sobre el Parquet
│   ├── exceptions.py             # handler que mapea validación → 422
│   ├── migrations/0001_initial.py
│   └── management/commands/load_transactions.py
└── tests/test_api.py             # 18 tests
```
