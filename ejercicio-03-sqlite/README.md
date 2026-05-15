# Ejercicio 3 — La Capa Transaccional

Capa transaccional implementada en SQLite optimizada para 5 patrones de acceso con SLAs estrictos, comparada empíricamente contra DuckDB sobre el Parquet del E1.

---

## Regenerar la base desde cero (un solo comando)

Desde la raíz del repo:

```bash
uv run python ejercicio-03-sqlite/ingest.py \
    --parquet data/benchmark_1m/transactions.snappy.parquet \
    --db ejercicio-03-sqlite/db/transactions_wal.db \
    --chunk-size 50000 --wal
```

Eso crea la tabla (sin índices) y carga 1 M filas en ~16 s. Los índices secundarios los aplica `benchmark_queries.py` para poder medir CON y SIN.

> Si el Parquet de 1 M filas no existe, regenéralo primero con:
> ```bash
> uv run python ejercicio-01-formatos/generate_data.py --size 1m
> uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m --formats parquet_snappy
> ```

---

## Reproducir el benchmark completo

```bash
# 1. Ingesta con WAL
uv run python ejercicio-03-sqlite/ingest.py \
    --db ejercicio-03-sqlite/db/transactions_wal.db --wal

# 2. Ingesta sin WAL (para comparar)
uv run python ejercicio-03-sqlite/ingest.py \
    --db ejercicio-03-sqlite/db/transactions_nowal.db --no-wal

# 3. Benchmark de los 5 patrones (sin idx, con idx, DuckDB)
uv run python ejercicio-03-sqlite/benchmark_queries.py
```

Outputs:
- `results/ingest.json` — tiempos de ingesta para ambos modos de journal.
- `results/benchmark.json` — mediciones detalladas (100 reps/patrón).
- `results/explain_query_plan.txt` — planes de SQLite + DuckDB.

---

## Estructura

```
ejercicio-03-sqlite/
├── schema.sql               # DDL de la tabla
├── schema_design.md         # justificación técnica de cada índice + SLAs
├── ingest.py                # CLI de ingesta (chunked + WAL opcional)
├── benchmark_queries.py     # CLI del benchmark (100 reps/patrón)
├── results/
│   ├── ingest.json
│   ├── benchmark.json
│   └── explain_query_plan.txt
├── db/                      # archivos .db generados (gitignored)
└── README.md                # este archivo
```

---

## Los 5 patrones y sus SLAs

| ID | Patrón | SLA | Cubierto por |
|---|---|---:|---|
| P1 | `WHERE transaction_id = ?` | <10 ms | PK implícita (`sqlite_autoindex_transactions_1`) |
| P2 | `WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20` | <50 ms | `idx_txns_user_timestamp` |
| P3 | `WHERE user_id = ? AND timestamp BETWEEN ? AND ?` | <50 ms | `idx_txns_user_timestamp` |
| P4 | `SELECT SUM(amount) WHERE user_id = ? AND timestamp >= ?` | <50 ms | `idx_txns_user_timestamp` |
| P5 | `WHERE country_code = ? GROUP BY user_id HAVING COUNT(*) > N` | <200 ms | `idx_txns_country_user` (covering) |

Justificación detallada de cada decisión: ver [`schema_design.md`](schema_design.md).

---

## Resultados — SLAs cumplidos (p95 sobre 100 reps)

| Patrón | SLA | SQLite **sin idx** (p95) | SQLite **con idx** (p95) | DuckDB sobre Parquet (p95) |
|---|---:|---:|---:|---:|
| P1 | 10 ms | **0.10 ms** ✅ | **0.08 ms** ✅ | 101.94 ms ❌ |
| P2 | 50 ms | 78.24 ms ❌ | **0.21 ms** ✅ | 112.45 ms ❌ |
| P3 | 50 ms | 78.67 ms ❌ | **0.08 ms** ✅ | 82.64 ms ❌ |
| P4 | 50 ms | 79.21 ms ❌ | **0.08 ms** ✅ | 26.60 ms ✅ |
| P5 | 200 ms | 158.48 ms ✅ | **10.40 ms** ✅ | 19.87 ms ✅ |

Con los dos índices secundarios, los **5 patrones cumplen su SLA con margen ≥19×**.

---

## Ingesta — WAL vs rollback journal

| Modo | Tiempo (1 M filas) | DB final | filas/s |
|---|---:|---:|---:|
| WAL (`journal_mode=WAL` + `synchronous=NORMAL`) | 15.93 s | 160.01 MB | 62 768 |
| Rollback journal (`journal_mode=DELETE` + `synchronous=FULL`) | 16.14 s | 160.01 MB | 61 960 |

**Diferencia: 1.3 %.** Es esperado: con sólo 20 commits (uno por chunk de 50 000 filas), el cuello de botella es la latencia de `fsync` por COMMIT, no el algoritmo del journal. La ventaja real de WAL aparece en concurrencia lector/escritor (los lectores no bloquean a los escritores), lo cual no estresa este benchmark.

Ambas ingestas terminan en <17 s — muy por debajo del SLA de 3 minutos del PDF (~9 % del límite).

---

## Comparación vs DuckDB — análisis patrón por patrón

DuckDB se evalúa **sobre el Parquet directamente** (`read_parquet()`), tal como funcionaría en un sistema dual donde SQLite atiende OLTP y DuckDB atiende analytics. No se crean índices ni materializaciones porque ese no es el modelo de uso de DuckDB.

### P1 — `WHERE transaction_id = ?`

| Engine | p50 | p95 | p99 |
|---|---:|---:|---:|
| SQLite (PK) | 0.06 ms | **0.08 ms** | 0.11 ms |
| DuckDB (Parquet) | 90.05 ms | 101.94 ms | 110.95 ms |

**Ganador: SQLite (~1 275×).** SQLite hace `SEARCH transactions USING INDEX sqlite_autoindex_transactions_1 (transaction_id=?)` — un B-tree lookup en log₂(1M) ≈ 20 comparaciones. DuckDB tiene que **escanear el Parquet entero** porque no hay forma de indexar puntualmente un valor de UUID dentro de un Parquet: sólo tiene los `min/max` por row group del estadístico, y un UUID4 cae casi aleatoriamente en cualquier row group, así que el predicate pushdown no descarta nada. Este es el caso paradigmático de "DuckDB no es para esto".

### P2 — `WHERE user_id = ? ORDER BY timestamp DESC LIMIT 20`

| Engine | p50 | p95 | p99 |
|---|---:|---:|---:|
| SQLite (idx user_timestamp DESC) | 0.15 ms | **0.21 ms** | 0.25 ms |
| DuckDB (Parquet) | 103.46 ms | 112.45 ms | 116.14 ms |

**Ganador: SQLite (~535×).** SQLite hace un range scan sobre el prefijo del índice donde `user_id = ?` y, como el índice está ordenado por `timestamp DESC`, los primeros 20 entries ya vienen en orden — no se necesita sort. DuckDB hace `READ_PARQUET → HASH_JOIN SEMI → TOP_N` (ver `results/explain_query_plan.txt`): debe escanear el Parquet, encontrar las filas del user_id, ordenarlas, y tomar el top 20. Aún con dynamic filters y projection pushdown el costo base del scan de Parquet domina.

### P3 — `WHERE user_id = ? AND timestamp BETWEEN ? AND ?`

| Engine | p50 | p95 | p99 |
|---|---:|---:|---:|
| SQLite (idx user_timestamp) | 0.05 ms | **0.08 ms** | 0.11 ms |
| DuckDB (Parquet) | 16.56 ms | 82.64 ms | 91.52 ms |

**Ganador: SQLite (~1 000×).** SQLite hace un range scan dentro del prefijo `user_id = ?` acotado por las dos cotas de timestamp. DuckDB escanea todo el Parquet — el predicate pushdown sobre `timestamp` puede descartar algunos row groups (los que tienen `max(timestamp) < cutoff_low`), pero `user_id = ?` no se puede usar para skip de row groups porque no hay índice de bloom ni nada equivalente sobre user_id, así que DuckDB termina filtrando fila por fila. La varianza alta en DuckDB (mean 29 ms, p95 82 ms) viene de qué tan suerte tuvo cada query al saltarse row groups: rangos que caen en zonas cubiertas por estadísticos van rápido; los que caen "en el medio" no.

### P4 — `SELECT SUM(amount) WHERE user_id = ? AND timestamp >= cutoff`

| Engine | p50 | p95 | p99 |
|---|---:|---:|---:|
| SQLite (idx user_timestamp) | 0.06 ms | **0.08 ms** | 0.10 ms |
| DuckDB (Parquet) | 21.87 ms | 26.60 ms ✅ | 29.68 ms |

**Ganador: SQLite (~330×).** Ambos engines cumplen el SLA aquí — DuckDB también baja de 50 ms — pero SQLite sigue ganando por dos órdenes de magnitud. DuckDB se beneficia mucho del **projection pushdown** sobre Parquet (sólo lee `amount`, `user_id`, `timestamp`) y de la operación `UNGROUPED_AGGREGATE` que es vectorizada y muy eficiente; por eso es el patrón donde DuckDB se ve mejor. SQLite no necesita columnar porque el índice ya filtra a las pocas filas relevantes (~30 filas/mes por user típico) y la suma vectoriza también dentro de SQLite.

### P5 — `WHERE country_code = ? GROUP BY user_id HAVING COUNT(*) > 20`

| Engine | p50 | p95 | p99 |
|---|---:|---:|---:|
| SQLite (idx country_user covering) | 8.13 ms | **10.40 ms** | 11.06 ms |
| DuckDB (Parquet) | 14.40 ms | 19.87 ms ✅ | 25.49 ms |

**Ganador: SQLite (~1.9×).** Este es el patrón **más cerrado** y el caso donde DuckDB se acerca. Ambos cumplen el SLA con margen. SQLite gana porque el covering index `(country_code, user_id)` deja al motor sin tocar el heap principal — todo el GROUP BY se hace sobre páginas del índice ya ordenadas. DuckDB es muy bueno en este tipo de agregación grouped (HASH_GROUP_BY vectorizado sobre 200 K filas filtradas), pero paga el costo de escanear el Parquet de nuevo aún con projection pushdown. Si el query tuviera además agregados sobre `amount` (`SUM`, `AVG`), DuckDB probablemente se acercaría más por el factor vectorización.

### Conclusión de la comparación

| Caso | Ganador | Multiplicador |
|---|---|---:|
| P1 lookup puntual | SQLite | 1 275× |
| P2 ranking por user | SQLite | 535× |
| P3 rango por user | SQLite | 1 000× |
| P4 agregado por user | SQLite | 330× |
| P5 agregación por country | SQLite | 1.9× |

**SQLite con índices gana los 5 patrones**, pero la magnitud importa: P1–P4 son catastróficos para DuckDB porque exigen acceso puntual (uno o pocas filas) y DuckDB siempre paga el costo de escanear un Parquet de 54 MB. P5 es competitivo porque es el único patrón **realmente analítico** — agrupa y agrega sobre cientos de miles de filas, justo donde el modelo columnar/vectorizado de DuckDB juega su fortaleza.

Donde **DuckDB ganaría** (no medido por estar fuera del scope del E3 pero implícito en E1/E2):

- Queries sin filtro selectivo (full table scans).
- Agregaciones globales sin GROUP BY puntual.
- Joins entre tablas grandes.
- Lectura de columnas individuales (column pruning).

La conclusión del E3 no es "SQLite es mejor". Es **"DuckDB no fue diseñado para acceso transaccional"**: la decisión correcta en un sistema real es tener **ambos** — SQLite para los 5 patrones de este ejercicio (los típicos del frontend de una app) y DuckDB para los queries analíticos del E2. Es justo lo que la rúbrica del E4 va a pedir construir.
