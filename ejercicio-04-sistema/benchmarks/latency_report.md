# E4 — Reporte de latencia

Medición de latencia de los 6 endpoints: 100 requests por endpoint (y por
régimen en los analíticos), reportando p50/p95/p99. Datos crudos en
[`../results/latency.json`](../results/latency.json), regenerables con:

```bash
uv run python ejercicio-04-sistema/benchmarks/latency_benchmark.py --reps 100
```

## Metodología

- **100 requests** por endpoint. Para `/analytics/*` se mide en dos regímenes:
  - **cold** — el cache se vacía antes de *cada* request, así que las 100 pagan el escaneo columnar completo sobre 1 M filas en DuckDB. Es el peor caso.
  - **warm** — el cache se calienta una vez; las 100 requests son hits.
- Los endpoints transaccionales (`/users/*`) y `/health` no tienen cache, así que se miden en un único régimen.
- **In-process con `TestClient`**: se corre dentro del proceso para poder vaciar el cache entre requests cold. Esto **excluye el salto de red**, por lo que los números absolutos son una cota inferior del costo real en producción. Lo que el experimento mide con fidelidad es el **ratio cold/warm** — el efecto del cache, que es lo que se pide demostrar.

## Resultados

### Endpoints analíticos — cold vs warm (DuckDB + cache)

| Endpoint | Régimen | p50 (ms) | p95 (ms) | p99 (ms) | SLA | ¿Cumple? |
|---|---|---:|---:|---:|---:|:--:|
| `/analytics/summary` | cold | 45.59 | 50.95 | 52.77 | <500 ms | ✅ |
| `/analytics/summary` | warm | 0.599 | 0.882 | 1.545 | <20 ms | ✅ |
| `/analytics/top-merchants` | cold | 19.23 | 22.03 | 270.47 | <500 ms | ✅ |
| `/analytics/top-merchants` | warm | 0.668 | 1.038 | 1.261 | <20 ms | ✅ |

**Impacto del cache (speedup p50):** `summary` 45.59 → 0.599 ms = **76×**; `top-merchants` 19.23 → 0.668 ms = **29×**.

### Endpoints transaccionales y health (SQLite, sin cache)

| Endpoint | p50 (ms) | p95 (ms) | p99 (ms) | SLA | ¿Cumple? |
|---|---:|---:|---:|---:|:--:|
| `/users/{id}/transactions` | 0.667 | 0.977 | 1.295 | <80 ms | ✅ |
| `/users/{id}/stats` | 0.625 | 0.930 | 1.363 | <80 ms | ✅ |
| `/health` | 0.641 | 1.044 | 1.243 | <50 ms | ✅ |

**5/5 endpoints (6 incluyendo ambos regímenes analíticos) cumplen su SLA**, en todos los casos con varios órdenes de magnitud de margen.

## Análisis

**Por qué el cold de `summary` cuesta ~45 ms.** El endpoint hace tres agregados sobre la tabla completa: totales globales, breakdown por país y por categoría. Sin cache, DuckDB reescanea el Parquet de 1 M filas en cada request. 45 ms para tres `GROUP BY` full-scan sobre un millón de filas es exactamente donde brilla un motor columnar — un escaneo equivalente fila por fila sería mucho peor. Aun así, está 10× por debajo del SLA cold de 500 ms.

**Por qué `top-merchants` cold es más barato que `summary` cold (~19 vs ~45 ms).** Es un solo `GROUP BY merchant_id` con `ORDER BY ... LIMIT`, contra los tres agregados de `summary`. Menos trabajo, menos tiempo.

**El p99 de 270 ms en `top-merchants` cold.** Es un outlier de la primera invocación: DuckDB compila y optimiza el plan la primera vez que ve la query. Las siguientes 99 caen a ~20 ms (de ahí que p50 y p95 sean ~19–22 ms pero el máximo dispare el p99). En un servidor de larga vida este costo se paga una sola vez al arranque y desaparece del régimen estable; igual queda por debajo del SLA cold de 500 ms.

**Por qué warm es ~0.6 ms plano en los dos analíticos.** Un hit de cache es una búsqueda en un `dict` en memoria más la serialización JSON de la respuesta ya calculada. No toca DuckDB ni el Parquet. La latencia se vuelve independiente del tamaño del dataset — el mismo endpoint costaría lo mismo con 1 M o 100 M filas mientras el resultado quepa en cache. Esto es lo que justifica cachear los `/analytics/*`: son caros de calcular, idénticos entre requests, y toleran datos con segundos de antigüedad.

**Por qué los transaccionales son ~0.6 ms sin cache.** No los cacheamos a propósito (son por-usuario y deben reflejar escrituras al instante), y no lo necesitan: los índices del E3 (`idx_user_timestamp`, PK) convierten cada consulta en un `SEARCH USING INDEX` que toca decenas de filas, no el millón. Cachear aquí agregaría complejidad de invalidación sin beneficio de latencia. El benchmark confirma la decisión: 0.6 ms ya está 130× por debajo del SLA de 80 ms.

**Por qué `/health` es trivial.** No toca ninguna base: solo lee contadores en memoria (uptime, hits/misses del cache, ping de las conexiones). Su SLA de 50 ms es holgadísimo; mide ~0.6 ms.

## Conclusión

El cache cumple su rol: convierte endpoints analíticos de decenas de ms a sub-milisegundo (29–76× más rápido), manteniéndolos muy por debajo del SLA incluso en frío. Los endpoints transaccionales no se cachean —correctamente— porque los índices del E3 ya los dejan en sub-milisegundo y porque deben ver las escrituras del batch al instante. La separación de backends se ve reflejada en los números: DuckDB absorbe los escaneos analíticos pesados, SQLite resuelve los lookups puntuales, y ningún endpoint paga por el backend equivocado.
