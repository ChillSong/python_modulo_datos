# E8 — Proyecto Final: Fintech LATAM

Sistema de monitoreo de transacciones. Integra los mejores elementos de E4–E7:
API FastAPI dual DuckDB+SQLite, pipeline ETL idempotente para CSV, detección de
anomalías, y todo desplegable con un solo comando Docker.

## Arranque rápido (Docker)

```bash
# Desde ejercicio-08-final/
docker compose up --build
```

El servicio `setup` construye el SQLite desde el Parquet del E1 (~4s).
El servicio `api` arranca una vez que `setup` termina con éxito.

```bash
# Verificar que la API está activa
curl http://localhost:8000/health

# Ver logs en tiempo real
docker compose logs -f api

# Apagar y eliminar el volumen
docker compose down -v
```

**Requisito previo:** el Parquet de 1M filas debe existir en `../data/benchmark_1m/`.
Si no existe, generarlo primero desde el E1:
```bash
uv run python ejercicio-01-formatos/generate_data.py --size 1m
uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m --formats parquet_snappy
```

## Arranque local (sin Docker)

```bash
# 1. Construir la base SQLite
uv run python ejercicio-08-final/setup_db.py

# 2. Levantar la API
cd ejercicio-08-final
uv run uvicorn app.main:app --reload
```

## Endpoints

| Método | Ruta | Backend | SLA |
|--------|------|---------|-----|
| GET | `/health` | — | <50ms |
| GET | `/analytics/summary` | DuckDB (Parquet) | <2s cold, <50ms warm |
| GET | `/analytics/top-merchants?limit=10&country=MX` | DuckDB | <2s cold |
| GET | `/anomalies?threshold=5&window_days=30` | DuckDB | <2s |
| GET | `/users/{id}/transactions?page=1&page_size=20` | SQLite | <200ms |
| GET | `/users/{id}/stats` | SQLite | <200ms |
| POST | `/transactions/batch` | SQLite | <2s (hasta 500 tx) |

### Ejemplos curl

```bash
# Resumen global
curl http://localhost:8000/analytics/summary

# Top 5 merchants en Brasil
curl "http://localhost:8000/analytics/top-merchants?limit=5&country=BR"

# Usuarios con >3 transacciones fallidas en los últimos 30 días
curl "http://localhost:8000/anomalies?threshold=3&window_days=30"

# Transacciones del usuario 42 (paginadas)
curl "http://localhost:8000/users/42/transactions?page=1&page_size=10"

# Estadísticas del usuario 42
curl http://localhost:8000/users/42/stats

# Insertar un batch (requiere JSON válido)
curl -X POST http://localhost:8000/transactions/batch \
  -H "Content-Type: application/json" \
  -d '{"transactions": [{"transaction_id": "550e8400-e29b-41d4-a716-446655440000",
       "timestamp": "2026-01-15T10:30:00", "user_id": 1, "merchant_id": 1,
       "amount": 99.99, "category": "Food", "country_code": "MX",
       "status": "completed"}]}'
```

## Pipeline ETL (CSV externo)

Procesa un CSV con el schema fijo del módulo, valida, carga en SQLite y genera
un reporte de ejecución.

```bash
# Local (desde ejercicio-08-final/)
cd ejercicio-08-final
uv run python -m pipeline.pipeline --csv /ruta/al/archivo.csv

# En Docker (corre en el mismo contenedor que la API)
docker compose run api python -m pipeline.pipeline \
  --csv /data/input.csv \
  --db /db/transactions.db
```

El pipeline es **idempotente**: procesarlo dos veces con el mismo CSV produce el
mismo estado en la base. Las filas con errores van a `quarantine/YYYY-MM-DD.jsonl`.

### Reporte de ejecución (ejemplo)

```json
{
  "run_id": "20260616_143022",
  "started_at": "2026-06-16T14:30:22",
  "total_seconds": 0.312,
  "source": {"type": "csv", "path": "/data/input.csv"},
  "extracted": 1000,
  "valid": 950,
  "rejected": {
    "total": 50,
    "by_reason": {"amount_out_of_range": 30, "invalid_category": 20}
  },
  "loaded": {"received": 950, "inserted": 940, "duplicates_skipped": 10}
}
```

## Tests

```bash
# Todos los tests (pipeline no requiere Parquet; API hace skip si faltan datos)
uv run pytest ejercicio-08-final/tests/ -v

# Solo tests del pipeline (siempre corren)
uv run pytest ejercicio-08-final/tests/ -v -k "pipeline or extract or transform or load"

# Solo tests de la API (requieren Parquet + SQLite)
uv run pytest ejercicio-08-final/tests/ -v -k "not (pipeline or extract or transform or load)"
```

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `PARQUET_PATH` | `../data/benchmark_1m/transactions.snappy.parquet` | Parquet del E1 |
| `DB_PATH` | `db/transactions.db` | SQLite transaccional |
| `DATA_DIR` | `../data` | Directorio montado en Docker |
| `CACHE_TTL_SUMMARY` | `30` | TTL cache en segundos (0 = off) |
| `CACHE_TTL_TOP_MERCHANTS` | `30` | TTL cache top-merchants |
| `CACHE_TTL_ANOMALIES` | `30` | TTL cache anomalías |

Copiar `.env.example` a `.env` para personalizar:
```bash
cp .env.example .env
```

## Arquitectura

```
                    ┌─────────────────────────────────┐
                    │          FastAPI (E8)            │
                    │                                 │
  Parquet ──────────┤  DuckDB (analytics + anomalías) │
  (E1, snapshot)    │    /analytics/* /anomalies      │
                    │                                 │
  SQLite ───────────┤  SQLite+WAL (OLTP)             │
  (transaccional)   │    /users/* /transactions/batch │
                    └─────────────────────────────────┘
                                   │
              ┌────────────────────┤
              ▼                    ▼
     Pipeline CSV               /health
     extract → transform → load   (in-process, <50ms)
     INSERT OR IGNORE (idempotente)
```

**Patrón OLTP/OLAP:** los inserts del batch van a SQLite; analytics lee el Parquet
histórico. Un registro nuevo NO se refleja en `/analytics/*` hasta que el Parquet
sea regenerado (decisión documentada en `decisions.md`).
