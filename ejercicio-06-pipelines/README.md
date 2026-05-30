# Ejercicio 6 — El Pipeline de Datos

Pipeline ETL (`Extract` → `Transform` → `Load`) que ingesta transacciones
nuevas en una base SQLite con el **mismo schema del E3**. Lee desde una fuente
simulada con errores deliberados, normaliza tipos, aplica las reglas de
negocio y carga las filas válidas. Las filas rechazadas no se pierden: van a
cuarentena con el motivo. La carga es **transaccional** e **idempotente** —
correrlo dos veces con los mismos datos deja la base en el mismo estado.

## Capas en una imagen

```
   data_source.py        extract.py           transform.py             load.py
  ┌───────────────┐    ┌──────────────┐    ┌────────────────┐    ┌────────────────┐
  │ batch crudo   │    │ ISO 8601     │    │ reglas de      │    │ INSERT OR      │
  │ (100-1000)    │ -> │ MAYUSC pais  │ -> │ negocio + cuar │ -> │ IGNORE atomico │
  │ + errores     │    │ amount 2dp   │    │ por dia (jsonl)│    │ INSERT OR      │
  └───────────────┘    └──────────────┘    └────────────────┘    └────────────────┘
            ▲                                       │                      │
            │                                       ▼                      ▼
        pipeline.py orquesta              quarantine/YYYY-MM-DD.jsonl   db/transactions.db
        results/run_YYYYMMDD_HHMMSS.json
```

Cada archivo tiene **una sola responsabilidad** (rubrica: capas separadas):

| Archivo | Hace | NO hace |
|---|---|---|
| `data_source.py` | Simula batches con errores deliberados, deterministica por `seed` | Normaliza ni valida |
| `extract.py` | Normaliza tipos/formatos (timestamps a ISO, paises a mayusculas, amount a 2 decimales) | Aplica reglas de negocio (no descarta nada) |
| `transform.py` | Aplica las 5 reglas del PDF + cuarentena con motivo | Toca la base |
| `load.py` | `INSERT OR IGNORE` transaccional + inicializa schema | Valida reglas (asume input ya valido) |
| `pipeline.py` | Orquesta las 4 capas, mide tiempos, escribe el reporte JSON | Implementa logica de las capas |

## Reglas de validacion (PDF, paso 3)

| Regla | Codigo de rechazo |
|---|---|
| `amount` ∈ [0.01, 5000.00] | `amount_out_of_range` |
| `category` en el dominio de 10 valores | `invalid_category` |
| `country_code` en el dominio de 15 paises | `invalid_country` |
| `timestamp` no mas de 1h en el futuro | `future_timestamp` |
| `transaction_id` es un UUID4 valido | `invalid_uuid` |
| Ningun campo obligatorio es `null` / vacio | `null_field` |
| `timestamp` parseable como datetime | `invalid_timestamp` |
| `amount` numerico | `invalid_amount_type` |

## Puesta en marcha (desde esta carpeta)

```bash
cd ejercicio-06-pipelines

# Correr el pipeline completo (genera batch simulado + reporte de la corrida)
uv run python pipeline.py

# Con flags personalizados
uv run python pipeline.py --batch-size 1000 --error-rate 0.2 --seed 7

# Cargar sobre la base del E3 en vez de la base local
uv run python pipeline.py --db ../ejercicio-03-sqlite/db/transactions_wal.db

# Generar un batch en disco y procesarlo aparte (modo "fuente externa")
uv run python data_source.py --batch-size 500 --error-rate 0.1 --seed 1 \
    --output inbox/batch_001.jsonl
uv run python pipeline.py --source-file inbox/batch_001.jsonl

# Tests
uv run pytest -q
```

La primera corrida crea `db/transactions.db` desde `schema.sql` (idempotente —
las siguientes corridas reusan la base) y `quarantine/YYYY-MM-DD.jsonl`.

## Idempotencia (rubrica)

Garantizada en dos niveles:

1. **Fuente:** `simulate_batch(size, error_rate, seed)` es deterministica.
   Para los mismos argumentos produce exactamente el mismo batch (mismos
   UUIDs, mismos errores, en el mismo orden).
2. **Carga:** `INSERT OR IGNORE` por `transaction_id` (PK). Si una corrida
   inserto la fila `X`, una corrida posterior con la misma `X` la cuenta como
   duplicada y la deja igual.

Verificacion: correr `pipeline.py` dos veces con el mismo seed deja la base
con la misma cantidad de filas y reporta `inserted=0, duplicates_skipped=N`
en la segunda corrida (test `test_pipeline_is_idempotent`).

> Nota: la cuarentena es un **log** (append-only); las filas rechazadas se
> escriben una vez por corrida, no se deduplican. La idempotencia es sobre el
> **estado final de la base**, que es lo que pide la rubrica.

## Reporte de ejecucion

Cada corrida escribe `results/run_YYYYMMDD_HHMMSS.json` con la siguiente
estructura:

```json
{
  "run_id": "20260529_165703",
  "started_at": "2026-05-29T16:57:03",
  "total_seconds": 0.017,
  "source": {
    "type": "simulated",
    "batch_size": 500,
    "error_rate": 0.15,
    "seed": 42
  },
  "extracted": 500,
  "valid": 425,
  "rejected": {
    "total": 75,
    "by_reason": {
      "amount_out_of_range": 28,
      "future_timestamp": 12,
      "invalid_category": 14,
      "invalid_country": 7,
      "invalid_uuid": 7,
      "null_field": 7
    }
  },
  "loaded": {
    "received": 425,
    "inserted": 425,
    "duplicates_skipped": 0
  },
  "db": ".../db/transactions.db",
  "quarantine_file": ".../quarantine/2026-05-29.jsonl"
}
```

Verificacion de cuadre: `extracted == valid + rejected.total` y
`loaded.received == loaded.inserted + loaded.duplicates_skipped`.

## Variables / argumentos del CLI

| Flag | Default | Para que |
|---|---|---|
| `--batch-size` | `500` | Tamano del batch simulado (debe estar entre 100 y 1000) |
| `--error-rate` | `0.1` | Fraccion de errores inyectados (0.0..1.0) |
| `--seed` | `42` | Semilla para el generador deterministico |
| `--source-file` | _none_ | Si se pasa, lee el batch desde un JSONL (en vez de simularlo) |
| `--db` | `db/transactions.db` | Path de la base SQLite destino |

## Estructura

```
ejercicio-06-pipelines/
├── schema.sql                  # tabla transactions (igual al E3)
├── data_source.py              # fuente simulada deterministica
├── extract.py                  # normalizacion (sin validar)
├── transform.py                # reglas de negocio + cuarentena
├── load.py                     # INSERT OR IGNORE transaccional
├── pipeline.py                 # orquestador + reporte de la corrida
├── tests/test_pipeline.py      # 22 tests (extract, transform, load, E2E)
├── db/                         # gitignored — la base generada
├── quarantine/                 # gitignored — YYYY-MM-DD.jsonl por dia
├── results/                    # gitignored — un JSON por corrida
└── README.md
```
