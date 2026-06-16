# Decisiones técnicas — E8 Proyecto Final

**Caso:** Fintech LATAM, monitoreo de transacciones. 1M filas, 8 campos, distribución de 1 año.

---

## 1. API: FastAPI sobre Django REST Framework

Elegí FastAPI porque el E4 y E5 lo compararon directamente en el mismo proyecto.

El E4 (FastAPI) alcanzó p50 <0.6ms en `/health` y <1ms en analytics con cache caliente. El E5 (Django REST Framework) tardó ~120 segundos en cargar el mismo Parquet con su ORM, y en latencia pura agrega entre 2–5ms por el middleware stack que DRF activa incluso en rutas simples. Para un sistema de monitoreo con picos de tráfico, esos milisegundos importan.

La otra ventaja concreta: FastAPI valida la entrada con Pydantic y emite HTTP 422 con detalle de campo sin código extra. En DRF hay que escribir un `EXCEPTION_HANDLER` custom (como hice en E5) para reescribir los 400 a 422. Menos código implica menos superficie de bugs.

**Trade-off aceptado:** FastAPI no incluye admin ni ORM. Para este caso (monitoreo, no gestión de contenido) eso no es una pérdida.

---

## 2. Analytics: DuckDB sobre Parquet

El E2 midió 8 queries idénticas sobre 1M filas con tres motores:
- pandas: 95s total, ~11.9s por query
- Polars (lazy): 5.3s total, ~0.66s por query
- DuckDB: 2.3s total, ~0.29s por query

DuckDB gana por su ejecución columnar vectorizada: no carga toda la fila, solo las columnas que necesita. Para `GROUP BY category COUNT(*)`, DuckDB escanea solo esa columna; pandas carga las 8.

El endpoint `/anomalies` es el caso que más justifica DuckDB: filtra por `status='failed'`, rango de fecha, agrupa por `user_id` y aplica `HAVING COUNT(*) > N`. Eso es un scan columnar con predicado + agregación — exactamente la carga donde DuckDB brilla. En la implementación, la query tarda <200ms sobre 1M filas sin índices adicionales porque DuckDB puede hacer early termination en el filtro de timestamp.

El Parquet con Snappy (E1: 140MB) vs CSV (280MB) reduce el I/O a la mitad. En un contenedor Docker donde el Parquet entra en disco local, eso importa en el primer scan (los siguientes son cache del SO).

**Trade-off aceptado:** DuckDB lee el Parquet como snapshot. Un insert en el batch endpoint NO se refleja en analytics. Es el patrón OLTP/OLAP deliberado: las transacciones nuevas entran al SQLite transaccional; el Parquet es el dataset histórico validado. Documentado en el `README.md`.

---

## 3. OLTP: SQLite con WAL e índices compuestos

El E3 midió 5 patrones de acceso con y sin índices:

| Patrón | Sin índice | Con índice | Factor |
|--------|-----------|------------|--------|
| P1: lookup por user_id | 230ms | 0.18ms | 1275x |
| P5: lookup por country+user | 280ms | 0.15ms | 1867x |

Los dos índices (`user_id, timestamp DESC`) y (`country_code, user_id`) son los mismos que el E3 justificó con datos empíricos. Los recrea `setup_db.py` después de la ingesta masiva (crear antes de insertar 1M filas los haría ~3x más lentos).

WAL permite lecturas concurrentes sin bloquear escrituras. Con `PRAGMA synchronous = NORMAL` y `cache_size = -200000` (200MB de cache), las lecturas por usuario llegan a <0.3ms consistentes.

**Trade-off aceptado:** SQLite no escala horizontalmente. Un solo archivo .db limita la escritura concurrente a un writer a la vez (WAL lo mitiga para lecturas, no para escrituras simultáneas). Para 1M filas y tráfico razonable, es suficiente.

---

## 4. Cache TTL en memoria

El E4 benchmark demostró: summary cold=45.6ms, warm=0.6ms (ratio 76x). top-merchants cold=19ms, warm=0.67ms (ratio 29x).

El cache no invalida en inserts porque analytics lee el Parquet (snapshot inmutable). La coherencia está garantizada por arquitectura, no por lógica de invalidación. TTL de 30s configurable por env var: si el evaluador quiere deshabilitar el cache, pone `CACHE_TTL_SUMMARY=0`.

---

## 5. Pipeline: ETL de 3 capas para CSV externo

La separación extract → transform → load del E6 produce pipelines idempotentes y debuggeables por capa. En E8 la fuente es un CSV externo (no datos sintéticos): `extract.py` convierte los strings de CSV a tipos Python antes de que `transform.py` aplique las reglas de negocio.

La idempotencia viene de `INSERT OR IGNORE` por `transaction_id` (PK natural). Correr el pipeline dos veces con el mismo CSV deja la base en el mismo estado. El reporte de corrida indica cuántas filas fueron insertadas vs duplicadas, y cuántas rechazadas por qué motivo.

---

## 6. Docker: imagen < 300MB con multi-stage build

El E7 demostró que una imagen de 299.8MB es alcanzable con:
1. `requirements.txt` mínimo (fastapi + uvicorn + duckdb, ~82MB de venv)
2. Strip de `.so` binarios (~10-15MB)
3. Eliminar pip/wheel/setuptools del venv final (~13MB)
4. Base `python:3.12-slim` (205MB)

El `setup_db.py` usa DuckDB para leer el Parquet (sin pyarrow/pandas), manteniendo el mismo `requirements.txt` para el setup y el runtime.

---

## Qué cambiaría con 100M filas

**Parquet**: 140MB (1M filas) → ~14GB (100M filas). No cabe en disco de un contenedor estándar. Habría que particionar por fecha o país con Hive partitioning:  
```
data/year=2025/month=06/transactions.parquet
```
DuckDB puede leer particiones con `read_parquet('data/**/*.parquet', hive_partitioning=true)` y pushear predicados de fecha al nivel de partición, evitando escanear archivos irrelevantes.

**SQLite**: El límite práctico es ~50-100M filas antes de degradar en tiempo de ingesta y tamaño del WAL. Con 100M filas, migraría a **PostgreSQL** (escrituras concurrentes, `COPY FROM` para bulk insert, índices parciales). El código de la API cambiaría poco: mismo patrón de connection pool, mismas queries.

**Query `/anomalies`**: Con 100M filas, el scan de los últimos 30 días (≈8M filas) tardaría varios segundos. La solución es mantener una **vista materializada incremental** de `(user_id, failed_count)` actualizada por el pipeline en cada corrida, en vez de escanear el Parquet completo en cada request.

**Cache**: Con más volumen de datos, el TTL de 30s puede no ser suficiente si la query tarda 10s en recalcularse. Pasaría de cache in-process a **Redis** para compartir el cache entre múltiples réplicas de la API y configurar TTLs de 5-15 minutos.

---

## Qué monitorearía en producción

**1. Latencia por endpoint (p50/p95/p99)**: Un aumento sostenido en p95 de `/analytics/summary` indica que el Parquet creció y el cache se está vaciando con demasiada frecuencia. Umbral de alerta: p95 > 2× la línea base del benchmark E4 (>100ms para summary).

**2. Tasa de errores 5xx**: Un spike en 5xx puede indicar que el SQLite se quedó sin espacio en disco o que el Parquet fue corrompido/borrado. Alerta inmediata si tasa_5xx > 1% durante 5 minutos.

**3. Hit rate del cache (endpoint `/health`)**: El campo `cache_hit_rate` ya está expuesto. Un hit rate < 50% bajo carga sostenida indica que el TTL es demasiado corto para el volumen de tráfico, o que hay demasiada variedad de parámetros únicos (muchos valores distintos de `threshold` en `/anomalies`).

**4. Tasa de rechazos del pipeline**: Un aumento en `rejected.total` del reporte de corrida puede indicar que el proveedor de datos cambió su schema (nuevo valor de `category`, formato de timestamp diferente). Alerta si `rejected.total / extracted > 5%` en una corrida.

**5. Detección proactiva de anomalías en el propio sistema**: El dataset tiene 10% de transacciones `failed` históricamente. Si en un día la tasa sube a >15%, puede ser una falla en un procesador de pagos externo, no en nuestra API. Alertar cuando `failed / total > 0.12` en el resumen diario del pipeline — antes de que lo reporte un usuario.
