# Ejercicio 4 — El Sistema Completo

API FastAPI con arquitectura dual **DuckDB (analytics) + SQLite (transaccional)**,
caching con TTL configurable, pipeline de ingesta validado y suite de tests.

| Documento | Contenido |
|---|---|
| [`architecture_decision.md`](architecture_decision.md) | Qué backend usa cada endpoint y por qué, justificado contra el benchmark |
| [`benchmarks/latency_report.md`](benchmarks/latency_report.md) | p50/p95/p99 por endpoint, cold vs warm, validación de SLAs |
| `app/` | `main.py` (endpoints + lifespan), `db.py` (backends), `cache.py` (TTL), `models.py` (Pydantic) |
| `tests/test_api.py` | 18 tests pytest |

## Arquitectura

```
                         ┌──────────────────────────────┐
   HTTP   ──────────────►│        FastAPI (uvicorn)       │
                         │   conexiones en lifespan       │
                         │   ┌────────────────────────┐   │
                         │   │  TTLCache (in-memory)  │   │  hits/misses ─► /health
                         │   │  /analytics/* (TTL 30s)│   │
                         │   └───────────┬────────────┘   │
                         └───────┬───────┴────────┬───────┘
                  analytics      │                │   transaccional
              (full-scan OLAP)   │                │  (lookups + writes)
                                 ▼                ▼
                       ┌──────────────────┐  ┌──────────────────┐
                       │   DuckDB :memory:│  │   SQLite (WAL)    │
                       │  VIEW txns =     │  │  transactions.db │
                       │  read_parquet()  │  │  + 2 índices E3  │
                       └────────┬─────────┘  └────────┬─────────┘
                                ▼                     ▼
                     data/benchmark_1m/        ejercicio-04-sistema/
                     transactions.snappy        db/transactions.db
                         .parquet (E1)            (lo crea setup_db.py)
```

- **DuckDB** lee el Parquet de 1 M filas del E1 — agregados analíticos.
- **SQLite** es la base del E3 (1 M filas + índices) — accesos por usuario y escrituras.
- El `POST /transactions/batch` escribe en SQLite; analytics corre sobre el snapshot Parquet (ver `architecture_decision.md`).

## Endpoints

| Método | Ruta | Descripción | SLA |
|---|---|---|---:|
| GET | `/analytics/summary` | Totales globales + breakdown por país y categoría | 500/20 ms |
| GET | `/analytics/top-merchants?limit=N&country=XX` | Top N merchants por volumen | 500/20 ms |
| GET | `/users/{id}/transactions?page=N&page_size=M` | Transacciones del usuario, paginadas | 80 ms |
| GET | `/users/{id}/stats` | Total, conteo, categoría más frecuente, país | 80 ms |
| POST | `/transactions/batch` | Hasta 500 transacciones, valida y deduplica | 2 s |
| GET | `/health` | Conexiones, hit rate del cache, uptime | 50 ms |

## Cómo arrancar

Desde la raíz del repo:

```bash
# 1. Construir la base SQLite (1M filas + 2 índices). Reproducible desde cero.
uv run python ejercicio-04-sistema/setup_db.py

# 2. Levantar el servidor.
uv run uvicorn app.main:app --app-dir ejercicio-04-sistema --reload
```

La API queda en `http://127.0.0.1:8000`; documentación interactiva en `/docs`.

```bash
# Ejemplos
curl 'http://127.0.0.1:8000/health'
curl 'http://127.0.0.1:8000/analytics/summary'
curl 'http://127.0.0.1:8000/analytics/top-merchants?limit=5&country=MX'
curl 'http://127.0.0.1:8000/users/1/stats'
curl -X POST 'http://127.0.0.1:8000/transactions/batch' \
     -H 'Content-Type: application/json' \
     -d '{"transactions":[{"transaction_id":"demo-1","timestamp":"2026-01-15T10:30:00","user_id":1,"merchant_id":42,"amount":99.95,"category":"Food","country_code":"MX","status":"completed"}]}'
```

> Si el Parquet de 1 M filas no existe, genéralo primero con el E1:
> ```bash
> uv run python ejercicio-01-formatos/generate_data.py --size 1m
> uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m --formats parquet_snappy
> ```

## Variables de entorno

| Variable | Default | Para qué |
|---|---|---|
| `CACHE_TTL_SUMMARY` | `30` | TTL (s) del cache de `/analytics/summary` |
| `CACHE_TTL_TOP_MERCHANTS` | `30` | TTL (s) del cache de `/analytics/top-merchants` |
| `E4_PARQUET` | `data/benchmark_1m/transactions.snappy.parquet` | Parquet que lee DuckDB |
| `E4_DB` | `ejercicio-04-sistema/db/transactions.db` | Base SQLite |

## Tests y benchmark

```bash
# Tests (18) — desde la raíz
uv run pytest ejercicio-04-sistema/tests -q

# Benchmark de latencia (100 req/endpoint, cold vs warm)
uv run python ejercicio-04-sistema/benchmarks/latency_benchmark.py --reps 100
```

## Estructura

```
ejercicio-04-sistema/
├── app/
│   ├── main.py          # FastAPI: lifespan + 6 endpoints
│   ├── db.py            # backends DuckDB + SQLite (init en lifespan)
│   ├── cache.py         # TTLCache con métricas de hit rate
│   └── models.py        # modelos Pydantic (422 si inválido)
├── tests/test_api.py    # 18 tests
├── benchmarks/
│   ├── latency_benchmark.py
│   └── latency_report.md
├── results/latency.json # mediciones crudas
├── setup_db.py          # construye db/transactions.db desde el Parquet
├── conftest.py          # hace importable `app` en pytest
├── architecture_decision.md
└── README.md
```
