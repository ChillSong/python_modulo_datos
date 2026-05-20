# E4 — Decisión de arquitectura

Qué backend sirve cada endpoint y por qué. Cada elección está respaldada por
el benchmark de latencia ([`benchmarks/latency_report.md`](benchmarks/latency_report.md))
y por los resultados de E2 (motor de consultas) y E3 (capa transaccional).

## Resumen

| Endpoint | Backend | Cache | SLA | Por qué este backend |
|---|---|:--:|---:|---|
| `GET /analytics/summary` | **DuckDB** (Parquet) | sí | 500/20 ms | Agregados full-scan sobre 1 M filas — territorio OLAP columnar |
| `GET /analytics/top-merchants` | **DuckDB** (Parquet) | sí | 500/20 ms | `GROUP BY merchant_id` sobre toda la tabla |
| `GET /users/{id}/transactions` | **SQLite** | no | 80 ms | Lookup puntual por `user_id` + `ORDER BY ts` — índice del E3 |
| `GET /users/{id}/stats` | **SQLite** | no | 80 ms | Agregado acotado a un usuario — índice del E3 |
| `POST /transactions/batch` | **SQLite** | invalida | 2 s/500 | Escritura transaccional con deduplicación por PK |
| `GET /health` | **ninguno** | — | 50 ms | Sólo lee contadores en memoria |

## Dos motores, dos perfiles de carga

La regla que ordena todo: **DuckDB para escaneos analíticos, SQLite para
accesos puntuales y escrituras.** No es preferencia, es la naturaleza de
cada motor.

- **DuckDB es columnar y vectorizado.** Para `SUM`/`AVG`/`GROUP BY` sobre la
  tabla entera lee sólo las columnas que la query toca y procesa en lotes.
  En E2 ya ganó los agregados analíticos contra pandas y polars; aquí sirve
  los `/analytics/*` leyendo el Parquet del E1 directamente vía
  `read_parquet` (mismo patrón de vista del E2, sin cargar a memoria).
- **SQLite es un B-tree orientado a fila con índices.** Para "las últimas 20
  transacciones del usuario X" o "el total del usuario X", el índice
  `idx_txns_user_timestamp` del E3 convierte la consulta en un
  `SEARCH USING INDEX` que toca decenas de filas, no el millón. En E3 SQLite
  con índices ganó los 5 patrones puntuales contra DuckDB (P1 hasta 1275×).

**El benchmark confirma que la asignación es correcta**, que es justo lo que
el PDF advierte que se revisa ("si eliges mal el backend para un endpoint se
verá en los benchmarks"):

- `summary` cold = 45.6 ms p50 para tres agregados full-scan: barato gracias
  a DuckDB columnar. El mismo trabajo fila por fila en SQLite sería mucho peor.
- `/users/*` = 0.6 ms p50 gracias a los índices de SQLite. Servir esto desde
  DuckDB (sin índice secundario, escaneo de Parquet) sería órdenes de magnitud
  más lento — exactamente el error que el benchmark delataría.

## Por qué `/analytics/*` se cachea y `/users/*` no

- **Analytics se cachea** porque es caro de calcular (decenas de ms), el
  resultado es idéntico entre usuarios y entre requests, y tolera datos con
  segundos de antigüedad. El cache lo lleva de ~45 ms a ~0.6 ms (76×, ver
  reporte de latencia). TTL configurable por endpoint (`CACHE_TTL_SUMMARY`,
  `CACHE_TTL_TOP_MERCHANTS`), default 30 s.
- **Los `/users/*` no se cachean** porque son por-usuario (baja tasa de
  re-lectura de la misma clave), deben reflejar al instante lo que entra por
  `POST /transactions/batch`, y ya están en sub-milisegundo por los índices.
  Cachear aquí agregaría complejidad de invalidación sin ganancia de latencia.

## El batch escribe en SQLite; analytics lee del Parquet

`POST /transactions/batch` inserta en **SQLite**. `/analytics/*` lee del
**Parquet** vía DuckDB. Son dos fuentes distintas, así que **una inserción
no se refleja en los agregados analíticos**. Esto es deliberado, no un bug:

- Modela el patrón real OLTP/OLAP: las escrituras entran al sistema
  transaccional (SQLite), y la capa analítica corre sobre un *snapshot*
  inmutable (el Parquet) que en un sistema real se refrescaría por un
  pipeline batch/CDC cada cierto tiempo.
- Mantiene a DuckDB sobre el formato donde es fuerte (columnar comprimido) en
  lugar de forzarlo a leer la base SQLite fila por fila, lo que anularía su
  ventaja en el benchmark.
- Como efecto secundario, hace los tests estables: insertar filas de prueba
  no altera los conteos de `/analytics/summary`.

El costo es *staleness* analítico acotado al período de refresco del snapshot.
Para este sistema —donde analytics es agregada y aproximada por naturaleza—
es un trade-off correcto. Si el requisito fuera "analytics debe ver cada
escritura al instante", la alternativa sería que DuckDB consultara la propia
SQLite (`sqlite_scan`), sacrificando la ventaja columnar.

## Conexiones en el lifespan, nunca por request

Tanto DuckDB como SQLite se abren **una sola vez en el lifespan de FastAPI**
y viven en `app.state`. El PDF lo marca explícitamente: abrir conexiones
dentro de un endpoint es un error de arquitectura que el benchmark de latencia
delataría (cada request pagaría el costo de conexión). Las funciones de
endpoint reciben los backends por inyección de dependencias y nunca abren nada.

**Concurrencia.** uvicorn corre los endpoints síncronos en un threadpool. Una
`sqlite3.Connection` no es segura para uso concurrente y una conexión DuckDB
tampoco debe compartirse a la ligera, así que cada backend se protege con su
propio `Lock`. El benchmark de latencia es secuencial, de modo que la
contención es nula. Para concurrencia real de lectura se usaría un pool de
conexiones de lectura sobre SQLite — el modo WAL (heredado del E3) lo permite
sin bloquear al escritor, que es justo el motivo por el que E3 dejó la base en
WAL pensando en este ejercicio.
