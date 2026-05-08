# Ejercicio 1 — Formatos Bajo la Lupa

Comparación empírica de **CSV, JSON Lines, Parquet (sin compresión, Snappy y Gzip)** sobre tres escalas (100 000, 500 000 y 1 000 000 filas). Las mediciones provienen de los JSON en `results/` generados por `benchmark_cli.py`.

## Metodología

- **Dataset:** schema fijo del módulo (8 columnas; transactions_id UUID4, timestamp, user_id, merchant_id, amount, category, country_code, status). Generado en memoria una sola vez por corrida; el tiempo de generación **no** cuenta como escritura.
- **Escritura:** `time.perf_counter()`, **3 repeticiones** por formato, se reporta el promedio. Antes de cada repetición se borra el archivo y se invoca `gc.collect()`.
- **Lectura completa:** una sola medición tras descartar el resultado anterior.
- **Lectura selectiva:** sólo las columnas `amount` y `category`. En CSV se usa `usecols=`; en Parquet se usa `columns=`; en JSONL se lee todo y se proyecta (el formato no soporta column pruning).
- **Pico de RAM:** `tracemalloc` sobre la operación de lectura completa. ⚠️ `tracemalloc` sólo rastrea allocations del heap de Python — los buffers en C de pyarrow son invisibles para él (ver Conclusiones).
- **Hardware:** Linux 6.17 / Python 3.12 / pandas + pyarrow. Los tiempos absolutos no son comparables entre máquinas, pero las relaciones entre formatos sí.

Reproducción:

```bash
uv run python ejercicio-01-formatos/benchmark_cli.py --size 100k
uv run python ejercicio-01-formatos/benchmark_cli.py --size 500k
uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m
uv run python ejercicio-01-formatos/make_charts.py
```

## Resultados

### Escala 100 000 filas

| Formato | Escritura (s) | Lectura full (s) | Lectura selectiva (s) | Tamaño (MB) | Pico RAM Python (MB) |
|---|---:|---:|---:|---:|---:|
| csv | 0.283 | 0.150 | 0.055 | 9.67 | 24.33 |
| jsonl | 0.254 | 0.429 | 0.387 | 20.97 | 256.60 |
| parquet_none | 0.038 | 0.018 | 0.005 | 6.69 | 0.02 |
| parquet_snappy | 0.053 | 0.017 | 0.005 | 5.96 | 0.02 |
| parquet_gzip | 0.852 | 0.025 | 0.006 | 4.16 | 0.02 |

### Escala 500 000 filas

| Formato | Escritura (s) | Lectura full (s) | Lectura selectiva (s) | Tamaño (MB) | Pico RAM Python (MB) |
|---|---:|---:|---:|---:|---:|
| csv | 1.399 | 0.728 | 0.228 | 48.32 | 121.52 |
| jsonl | 1.229 | 2.047 | 2.012 | 104.82 | 1 283.28 |
| parquet_none | 0.127 | 0.054 | 0.013 | 31.22 | 0.02 |
| parquet_snappy | 0.205 | 0.078 | 0.016 | 27.48 | 0.02 |
| parquet_gzip | 3.851 | 0.135 | 0.018 | 18.80 | 0.02 |

### Escala 1 000 000 filas

| Formato | Escritura (s) | Lectura full (s) | Lectura selectiva (s) | Tamaño (MB) | Pico RAM Python (MB) |
|---|---:|---:|---:|---:|---:|
| csv | 2.810 | 1.461 | 0.438 | 96.65 | 242.99 |
| jsonl | 2.440 | 4.008 | 4.014 | 209.65 | 2 566.76 |
| parquet_none | 0.203 | 0.094 | 0.027 | 61.73 | 0.02 |
| parquet_snappy | 0.360 | 0.133 | 0.035 | 54.18 | 0.02 |
| parquet_gzip | 7.438 | 0.262 | 0.032 | 36.92 | 0.02 |

## Gráficas

![Tiempo de lectura completa](charts/read_full_time.png)

![Tiempo de lectura selectiva](charts/read_selective_time.png)

![Tamaño en disco](charts/file_size.png)

![Tiempo de escritura](charts/write_time.png)

> Las gráficas de tiempo usan escala logarítmica porque la diferencia entre Parquet y JSON Lines es de uno a dos órdenes de magnitud — en escala lineal las barras de Parquet desaparecen visualmente.

## Cómo cambia el comportamiento al escalar

- **Tamaño en disco:** crece de forma estrictamente lineal con el número de filas en los cinco formatos (10× filas ≈ 10× bytes). No hay overhead fijo significativo.
- **Tiempos:** también escalan casi linealmente. La pendiente, sin embargo, varía dos órdenes de magnitud entre formatos: leer el dataset de 1 M filas tarda ~4 s en JSONL contra ~0.1 s en Parquet.
- **Brecha relativa:** la ventaja de Parquet sobre CSV/JSONL **no se cierra al escalar** — al contrario, se vuelve más relevante en términos absolutos. Pasar de 100 k a 1 M agrega 1.3 s de lectura a CSV pero sólo 0.08 s a Parquet+Snappy.
- **Pico de RAM en JSONL** crece linealmente con las filas (~2.5 KB de Python por fila, dominados por dicts y strings intermedios). En 1 M filas ya rebasa 2.5 GB — esto importa: significa que `pd.read_json(lines=True)` no es viable para datasets que se acerquen al RAM disponible.
- **Compresión en Parquet:** la razón de compresión es estable a lo largo de las escalas (gzip ≈ 60 % del tamaño de none; snappy ≈ 88 %). Eso confirma que la compresión trabaja sobre el contenido, no sobre overhead estructural.

## Conclusiones

**Parquet domina en cualquier métrica relacionada con lectura.** Sobre 1 M filas, Parquet+Snappy lee el dataset completo 11× más rápido que CSV y 30× más rápido que JSONL; en lectura selectiva (sólo `amount` y `category`) la ventaja crece a 12× contra CSV y 115× contra JSONL. La razón es estructural: Parquet es un formato **binario y columnar**, así que no necesita convertir bytes ASCII a tipos numéricos (CSV/JSONL pagan ese coste fila por fila, especialmente caro para floats), guarda metadatos de tipos una sola vez por columna, y permite saltarse columnas que no se piden sin tocar sus bytes en disco. CSV y JSONL son formatos orientados a fila: cada columna está intercalada con las demás, así que la única forma de "leer dos columnas" es parsear el archivo entero y descartar el resto.

**Por qué JSONL es la peor opción para datos tabulares.** El archivo es ~2.2× más grande que CSV porque cada fila repite los nombres de las columnas como strings JSON, además de comillas, dos puntos y llaves. La lectura es ~3× más lenta que CSV porque el parser de JSON es más complejo (más estados, escape de caracteres, etc.). Pero el costo más serio es **la memoria pico durante la lectura**: `pd.read_json(lines=True)` deserializa cada línea a un `dict` de Python antes de ensamblar el DataFrame. Para 1 M filas con 8 campos cada una, son ~8 millones de objetos Python intermedios; la medición de `tracemalloc` de 2.5 GB lo confirma. JSONL sólo se justifica cuando el esquema de cada fila es genuinamente heterogéneo — para datos tabulares con esquema fijo es la peor combinación.

**El compromiso de compresión en Parquet.** `parquet_none` es el más rápido en escritura entre los Parquet (0.20 s para 1 M filas) y prácticamente idéntico en lectura. `parquet_snappy` reduce el tamaño ~12 % a un costo de escritura ~80 % mayor (todavía 8× más rápido que CSV). `parquet_gzip` logra la mejor compresión (40 % menos disco que `parquet_none`) pero su escritura es ~37× más lenta que `parquet_none` y 21× más lenta que CSV. Esto pasa porque gzip es un codec secuencial CPU-bound optimizado para ratio de compresión; Snappy se diseñó explícitamente para velocidad sacrificando un 10–15 % de ratio. Importante: **la compresión sale gratis en lectura** — Parquet+Gzip lee 6× más rápido que CSV sin comprimir, porque el costo de descompresión es menor que el ahorro de I/O.

**Limitaciones del experimento.** `tracemalloc` muestra ~0 MB para los Parquet porque pyarrow asigna buffers en heaps de C/Arrow, fuera del control del recolector de memoria de Python. La RSS real del proceso al leer Parquet **no** es cero (rondará ~50–100 MB para 1 M filas), pero queda invisible para esta herramienta. Para una medición fiel se necesitaría `psutil.Process().memory_info().rss` o `resource.getrusage`. Aun así, la métrica sirve como cota inferior y muestra el efecto real: CSV y JSONL pagan su parsing en allocations de Python, Parquet no. Las mediciones se hicieron con disco SSD local y archivos calientes en page cache; un benchmark contra disco frío o S3 ampliaría aún más la ventaja relativa de Parquet (menos bytes leídos, menos round-trips).

**Recomendación final.** Para cualquier capa analítica que se lea más de lo que se escribe (lo más común en sistemas de datos modernos), la elección por defecto debe ser **Parquet+Snappy**: balance óptimo entre tamaño, velocidad de escritura, velocidad de lectura, y soporte universal en motores como DuckDB, Spark, BigQuery, Polars y pyarrow. Para almacenamiento frío o archivado donde el espacio en disco es el cuello de botella y los datos se leen rara vez, **Parquet+Gzip** paga su escritura lenta con un 40 % menos de disco y red. CSV se justifica solo como interfaz de intercambio con humanos o sistemas legados, no como formato de almacenamiento principal. JSON Lines no se justifica para datos tabulares con esquema fijo.
