# Ejercicio 3 — La Capa Transaccional

Capa transaccional en SQLite optimizada para 5 patrones de acceso con SLAs estrictos, comparada empíricamente contra DuckDB sobre el Parquet del E1.

| Documento | Contenido |
|---|---|
| [`schema_design.md`](schema_design.md) | Justificación técnica de cada decisión de schema y de los 2 índices secundarios |
| [`report.md`](report.md) | Resultados: ingesta WAL vs no-WAL, validación de SLAs, EXPLAIN, comparación vs DuckDB |
| `results/` | JSONs con mediciones crudas + EXPLAIN QUERY PLANs |

---

## Regenerar la base desde cero (un solo comando)

Desde la raíz del repo:

```bash
uv run python ejercicio-03-sqlite/ingest.py \
    --parquet data/benchmark_1m/transactions.snappy.parquet \
    --db ejercicio-03-sqlite/db/transactions_wal.db \
    --chunk-size 50000 --wal
```

Carga 1 M filas en ~16 s en una tabla sin índices secundarios. Los índices los aplica `benchmark_queries.py` para poder medir CON y SIN.

> Si el Parquet de 1 M filas no existe, regenéralo primero con:
> ```bash
> uv run python ejercicio-01-formatos/generate_data.py --size 1m
> uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m --formats parquet_snappy
> ```

---

## Reproducir el benchmark completo

```bash
# 1. Ingesta con WAL (default)
uv run python ejercicio-03-sqlite/ingest.py \
    --db ejercicio-03-sqlite/db/transactions_wal.db --wal

# 2. Ingesta sin WAL (para comparación)
uv run python ejercicio-03-sqlite/ingest.py \
    --db ejercicio-03-sqlite/db/transactions_nowal.db --no-wal

# 3. Benchmark: 5 patrones × {sin idx, con idx, DuckDB} × 100 reps
uv run python ejercicio-03-sqlite/benchmark_queries.py
```

Outputs en `results/`:
- `ingest.json` — tiempos de ingesta WAL y no-WAL.
- `benchmark.json` — mediciones por patrón (100 reps c/u) + EXPLAIN incrustado.
- `explain_query_plan.txt` — planes SQLite y DuckDB lado a lado.

El análisis de estos archivos vive en [`report.md`](report.md).

---

## Estructura

```
ejercicio-03-sqlite/
├── schema.sql               # DDL de la tabla
├── schema_design.md         # justificación técnica de cada decisión
├── ingest.py                # CLI de ingesta (chunked + WAL opcional)
├── benchmark_queries.py     # CLI del benchmark (100 reps/patrón)
├── results/
│   ├── ingest.json
│   ├── benchmark.json
│   └── explain_query_plan.txt
├── report.md                # análisis consolidado
├── db/                      # .db generados (gitignored)
└── README.md                # este archivo
```

---

## Los 5 patrones y sus SLAs (resumen — detalle en report.md)

| ID | Patrón | SLA | Cubierto por |
|---|---|---:|---|
| P1 | `WHERE transaction_id = ?` | <10 ms | PK implícita |
| P2 | `WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20` | <50 ms | `idx_txns_user_timestamp` |
| P3 | `WHERE user_id = ? AND timestamp BETWEEN ? AND ?` | <50 ms | `idx_txns_user_timestamp` |
| P4 | `SUM(amount) WHERE user_id = ? AND timestamp >= ?` | <50 ms | `idx_txns_user_timestamp` |
| P5 | `WHERE country_code = ? GROUP BY user_id HAVING COUNT(*) > N` | <200 ms | `idx_txns_country_user` (covering) |

**Cumplimiento (p95 sobre 100 reps con índices):** 5/5 SLAs cumplidos, margen mínimo 19× (P5), margen típico >300× (P1–P4). Justificación de cada índice en [`schema_design.md`](schema_design.md); análisis completo en [`report.md`](report.md).
