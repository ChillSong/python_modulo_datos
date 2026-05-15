# Schema Design — Ejercicio 3

Este documento justifica **cada decisión** de diseño del schema y de los índices a partir de:

1. Los 5 patrones de acceso del PDF y sus SLAs.
2. Las propiedades del dataset (1 M filas, schema fijo del módulo).
3. Datos empíricos del benchmark (`results/benchmark.json`).

---

## 1. Tipos de columna

| Columna | Tipo SQLite | Justificación |
|---|---|---|
| `transaction_id` | `TEXT PRIMARY KEY` | UUID4 (36 chars). La PK natural es la única columna realmente única — usarla como PK evita un índice extra. SQLite genera un B-tree implícito (`sqlite_autoindex_transactions_1`) que **cubre P1 sin necesidad de declarar un `idx_*`**. |
| `timestamp` | `TEXT NOT NULL` | SQLite no tiene tipo `DATETIME` nativo: `TIMESTAMP` se almacena igual que `TEXT` o `INTEGER`. Se elige ISO 8601 (`2026-04-30T15:06:00.123456`) porque el orden lexicográfico **coincide con el orden cronológico**, lo que permite que los range scans (`>= ? AND < ?`) en P3 y P4 funcionen sin parseo. La alternativa de guardar como `INTEGER` (epoch ms) sería ~4 bytes menos por fila pero pierde legibilidad humana y requiere conversión en cada `SELECT`. |
| `user_id` | `INTEGER` | Rango 1..50 000 → cabe en 2 bytes en SQLite (storage class INTEGER usa el espacio mínimo). |
| `merchant_id` | `INTEGER` | Igual. |
| `amount` | `REAL` | 0.01..5 000.00 → 8 bytes IEEE 754 (SQLite no tiene DECIMAL nativo; usar TEXT añadiría parseo). |
| `category`, `country_code`, `status` | `TEXT NOT NULL` | Cardinalidades chicas (10/15/3) pero **no** se normaliza a tablas de lookup: la regla del PDF dice que el schema del E1 se reutiliza tal cual en E2/E3/E4. Mantener strings directos preserva esa restricción y simplifica la ingesta desde Parquet. |

**Decisión a propósito de NULL:** todas las columnas son `NOT NULL` porque el generador de E1 garantiza valores válidos en todas. Permitir NULL invalida los índices parciales (no aplica aquí) y abre la puerta a `NULL = NULL` ambiguo en filtros futuros.

---

## 2. Índice primario (implícito, ya creado)

```sql
PRIMARY KEY (transaction_id)
-- equivale a: CREATE UNIQUE INDEX sqlite_autoindex_transactions_1 ON transactions(transaction_id)
```

**Cubre P1** (`SELECT * FROM transactions WHERE transaction_id = ?`).

**Por qué B-tree y no hash:** SQLite no tiene índices hash. Pero incluso si los tuviera, B-tree es la elección correcta aquí porque:

- Permite range scans (no nos sirve para P1 directamente, pero es la decisión por defecto consistente).
- El costo de un lookup exacto en un B-tree de 1 M entradas es O(log₂(1M)) ≈ 20 comparaciones de strings — completamente dominado por la latencia del fetch de la fila, no por el árbol.

**Evidencia empírica (P1, 100 reps):**

| Escenario | p50 | p95 | p99 | SLA |
|---|---:|---:|---:|---:|
| Sin índices secundarios | 0.08 ms | 0.10 ms | 0.11 ms | 10 ms |
| Con índices secundarios | 0.06 ms | 0.08 ms | 0.11 ms | 10 ms |

La PK por sí sola deja P1 ~125× por debajo del SLA. Los índices secundarios no aportan ni quitan a este patrón.

---

## 3. Índice secundario `idx_txns_user_timestamp`

```sql
CREATE INDEX idx_txns_user_timestamp
    ON transactions (user_id, timestamp DESC);
```

**Cubre P2, P3 y P4** — un solo índice para tres patrones distintos.

### Por qué composite y por qué este orden de columnas

Los tres patrones filtran por `user_id` (alta selectividad: 1/50 000 = 0.002 % de las filas) y luego operan sobre `timestamp` (orden o rango). La regla del leftmost-prefix obliga a poner `user_id` primero: cualquier query que mencione `user_id` (incluso sin `timestamp`) usa el índice. Si el orden fuera invertido, P2/P3/P4 quedarían sin índice porque ninguno filtra timestamp sin user_id.

### Por qué `timestamp DESC` y no `ASC`

La rúbrica del PDF para P2 dice "ordenadas por timestamp" pero no especifica dirección. La operación natural sobre transacciones es **ver las más recientes primero**, por eso elijo DESC. El efecto en el plan se ve en el EXPLAIN:

- Con `DESC` declarado: `SEARCH transactions USING INDEX idx_txns_user_timestamp (user_id=?)` — el `ORDER BY timestamp DESC LIMIT 20` se materializa con un **range scan directo** sobre el primer prefijo del índice, sin sort adicional.
- Si fuera `ASC`, SQLite tendría que recorrer el índice de atrás hacia adelante o aplicar un sort intermedio — costos pequeños pero medibles a escala.

### Decisión sobre covering index (DATA-DRIVEN)

P4 (`SELECT SUM(amount) WHERE user_id = ? AND timestamp >= ?`) **podría** beneficiarse de un covering index que incluya `amount`:

```sql
-- alternativa considerada
CREATE INDEX idx_txns_user_timestamp_amount
    ON transactions (user_id, timestamp DESC, amount);
```

Esto convertiría P4 en un **index-only scan** (no tendría que tocar la tabla principal para leer `amount`). El costo: ~8 bytes extra por fila × 1 M = **~8 MB de índice adicional**.

**Decisión empírica:** medir P4 sin covering primero. Los datos del benchmark (100 reps con índices):

| Patrón | p50 | p95 | p99 | SLA | Margen |
|---|---:|---:|---:|---:|---:|
| P4 sin covering | 0.06 ms | 0.08 ms | 0.10 ms | 50 ms | **625× bajo el SLA** |

P4 cumple con margen sobrado. Agregar el covering index sería **optimización prematura**: gastaría 8 MB de almacenamiento, alentaría la ingesta (más bytes a escribir por fila indexada), e iría contra el principio de "el índice más simple que cumpla el SLA". La fila completa (~250 bytes con sus strings de transaction_id y timestamp) cabe en una sola página de SQLite (4 KB por default), así que el lookup desde el índice al heap es una sola página por fila — costo despreciable a la escala de P4 (típicamente <50 filas por mes por user).

**Decisión final:** no covering. Documentar la opción y la razón por la que se descarta.

### EXPLAIN QUERY PLAN sin / con índice

```
P2 SIN idx:  [4|0] SCAN transactions
             [25|0] USE TEMP B-TREE FOR ORDER BY
P2 CON idx:  [5|0] SEARCH transactions USING INDEX idx_txns_user_timestamp (user_id=?)

P3 SIN idx:  [2|0] SCAN transactions
P3 CON idx:  [3|0] SEARCH ... USING INDEX idx_txns_user_timestamp (user_id=? AND timestamp>? AND timestamp<?)

P4 SIN idx:  [3|0] SCAN transactions
P4 CON idx:  [4|0] SEARCH ... USING INDEX idx_txns_user_timestamp (user_id=? AND timestamp>?)
```

El paso de `SCAN` a `SEARCH USING INDEX` explica los tres saltos cuantitativos:

| Patrón | Sin idx (p95) | Con idx (p95) | Mejora |
|---|---:|---:|---:|
| P2 | 78.24 ms | 0.21 ms | **373×** |
| P3 | 78.67 ms | 0.08 ms | **983×** |
| P4 | 79.21 ms | 0.08 ms | **990×** |

---

## 4. Índice secundario `idx_txns_country_user`

```sql
CREATE INDEX idx_txns_country_user
    ON transactions (country_code, user_id);
```

**Cubre P5.**

### Por qué composite y por qué este orden

P5 (`WHERE country_code = ? GROUP BY user_id HAVING COUNT(*) > N`) hace dos cosas:

1. Filtra por `country_code` (selectividad media: 1/15 ≈ 6.7 % de las filas — el filtro elimina ~93 % de la tabla).
2. Agrupa por `user_id`.

Con el orden `(country_code, user_id)`, SQLite hace:

1. Un **range scan** sobre el prefijo del índice donde `country_code = ?` (~66 000 filas en lugar de 1 M).
2. Como el índice está ordenado por `user_id` dentro de cada `country_code`, los grupos para el `GROUP BY user_id` ya vienen **ordenados** — SQLite puede agruparlos en un solo paso sin tabla temporal (en SCAN puro requería `USE TEMP B-TREE FOR GROUP BY`).
3. El índice contiene **ambas** columnas requeridas por el SELECT (`country_code`, `user_id`) y por el aggregate (`COUNT(*)`). EXPLAIN lo confirma: `SEARCH ... USING **COVERING INDEX** idx_txns_country_user (country_code=?)`. SQLite detecta automáticamente que es covering y nunca toca la tabla principal.

### Por qué este orden y no `(user_id, country_code)`

El orden inverso sería catastrófico para P5: tendría que recorrer **todos** los grupos de `user_id` y filtrar `country_code` dentro de cada uno. Para 50 000 user_ids esto es casi tan lento como un SCAN completo.

### Decisión NO tomada: índice sobre `country_code` solo

Considerado y descartado. Un índice de una sola columna `country_code` también filtraría el prefijo, pero al agrupar por `user_id` SQLite tendría que ir a la tabla principal a leer cada `user_id` — perdiendo el comportamiento covering. Agregar la segunda columna al índice cuesta 4 bytes/fila × 1 M = 4 MB extra; lo gana de sobra al eliminar el lookup al heap.

### EXPLAIN QUERY PLAN sin / con índice

```
P5 SIN idx:  [6|0] SCAN transactions
             [10|0] USE TEMP B-TREE FOR GROUP BY
P5 CON idx:  [6|0] SEARCH transactions USING COVERING INDEX idx_txns_country_user (country_code=?)
```

| Sin idx (p95) | Con idx (p95) | Mejora |
|---:|---:|---:|
| 158.48 ms | 10.40 ms | **15×** |

P5 es el único patrón cuya mejora con índice es de orden 10× en lugar de 100–1 000×. La razón es que **incluso con índice**, P5 toca ~66 000 entradas (las filas de un país) — no es una búsqueda puntual como P1–P4. El índice elimina el sort temporal pero no el tamaño del resultado intermedio. Aún así, queda **20× bajo el SLA de 200 ms**.

---

## 5. Resumen del costo en almacenamiento

| Etapa | DB total | Δ |
|---|---:|---:|
| Sólo tabla (post-ingest) | 160.05 MB | — |
| Tras crear los 2 índices | 252.11 MB | **+92.06 MB (+57.5 %)** |

Los índices casi duplican el espacio. Es la expectativa para una capa transaccional con queries selectivos a alta velocidad — el espacio en disco es barato comparado con responder en <50 ms.

Construir los 2 índices después de la ingesta tarda **2.88 s**. Crearlos antes y luego insertar 1 M filas habría sido drásticamente más lento porque cada INSERT tendría que mantener los B-trees en orden (orden ~`O(n log n)` por fila). La estrategia de "ingest first, index after" es estándar para bulk loads.

---

## 6. Pragmas (no son schema pero afectan el plan)

Configurados en `ingest.py` antes de la creación del schema y reutilizados en `benchmark_queries.py`:

| Pragma | Valor | Razón |
|---|---|---|
| `journal_mode` | `WAL` (default) o `DELETE` con `--no-wal` | Probado en ambos modos. Para bulk ingest la diferencia es <2 % (15.93 s vs 16.14 s para 1 M filas) porque el cuello de botella es el fsync por COMMIT, no el algoritmo de journal. La ventaja real de WAL aparece en concurrencia lector/escritor, que el benchmark no estresa. |
| `synchronous` | `NORMAL` con WAL, `FULL` sin WAL | `NORMAL` con WAL pierde como máximo la última transacción no fsynced ante crash del kernel; `FULL` con rollback journal es la única manera de no perder transacciones cometidas. |
| `cache_size` | `-200000` (200 MB) | La DB cabe en RAM con margen. El cache evita ir a disco en lecturas calientes (esencial para los <1 ms de P1–P4 con índices). |
| `temp_store` | `MEMORY` | Los `TEMP B-TREE FOR ORDER BY` / `GROUP BY` no tocan disco. Mejora marginal aquí porque con índices ya no se usan, pero es protección contra futuros queries no anticipados. |

---

## 7. Cumplimiento de SLAs (resumen ejecutivo)

| Patrón | SLA | p95 sin idx | p95 con idx | Cumple |
|---|---:|---:|---:|:---:|
| P1 | <10 ms | 0.10 ms | 0.08 ms | ✅ |
| P2 | <50 ms | 78.24 ms ❌ | 0.21 ms | ✅ |
| P3 | <50 ms | 78.67 ms ❌ | 0.08 ms | ✅ |
| P4 | <50 ms | 79.21 ms ❌ | 0.08 ms | ✅ |
| P5 | <200 ms | 158.48 ms | 10.40 ms | ✅ |

**Sin índices secundarios:** P2, P3 y P4 fallan; P1 y P5 cumplen pero por motivos distintos (PK implícita en P1; HAVING sobre table scan todavía dentro del límite generoso en P5).

**Con los dos índices propuestos:** los 5 patrones cumplen su SLA con margen de al menos 19×, en su mayoría >300×.

---

## 8. Lo que NO se hizo (y por qué)

- **No se creó un índice sobre `(merchant_id, ...)`.** Ningún patrón lo requiere. Los índices cuestan espacio y alentan los INSERTs; sólo se justifican por queries reales, no especulativas.
- **No se normalizó `category`/`country_code`/`status` a tablas de lookup.** El schema del módulo es fijo (regla del PDF). Además, en SQLite el costo de un string repetido es bajo: cada valor único se guarda como dato pero los índices secundarios pueden trabajar sobre strings cortos sin penalty significativo.
- **No se usaron índices parciales** (`WHERE status = 'completed'`). Podrían servir para queries futuras que sólo consideren transacciones exitosas, pero ningún patrón actual los requiere.
- **No se creó un índice covering para P4.** Razonado en §3 — empíricamente no es necesario.
