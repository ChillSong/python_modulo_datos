# Ejercicio 7 — De tu Máquina al Mundo

API del E4 (FastAPI + DuckDB + SQLite) contenerizada con Docker.  
Arranca con un solo comando; los datos **nunca** se bajan a la imagen.

## Prerrequisito

El Parquet de 1 M filas debe existir en `data/benchmark_1m/transactions.snappy.parquet`  
(generado por E1). Si no existe, créalo desde la raíz del repo:

```bash
uv run python ejercicio-01-formatos/generate_data.py --size 1m
uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m --formats parquet_snappy
```

## Arranque

```bash
cd ejercicio-07-contenedores

# Copiar y ajustar variables de entorno (opcional — los defaults ya funcionan)
cp .env.example .env

# Construir imagen y levantar servicios
docker compose up --build
```

Al terminar el build, `setup` genera el SQLite y luego `api` arranca.  
La API queda disponible en `http://localhost:8000`.

## Verificar que funciona

```bash
# Estado de los servicios
docker compose ps

# Healthcheck
curl http://localhost:8000/health

# Endpoints de analytics
curl http://localhost:8000/analytics/summary
curl "http://localhost:8000/analytics/top-merchants?limit=5&country=MX"

# Endpoints de usuario
curl http://localhost:8000/users/1/stats
curl "http://localhost:8000/users/1/transactions?page=1&page_size=5"
```

## Logs

```bash
# Seguir los logs de la API en tiempo real (formato JSON)
docker compose logs -f api
```

Cada línea es un objeto JSON con `timestamp`, `level` y `message`.

## Parar y limpiar

```bash
# Parar contenedores
docker compose down

# Parar y eliminar volumen de SQLite (requiere regenerar en el próximo arranque)
docker compose down -v
```

## Variables de entorno

Definidas en `.env` (copia de `.env.example`):

| Variable | Default | Descripción |
|---|---|---|
| `DATA_DIR` | `../data` | Ruta al directorio con el Parquet (bind mount) |
| `CACHE_TTL_SUMMARY` | `30` | TTL en segundos para `/analytics/summary` |
| `CACHE_TTL_TOP_MERCHANTS` | `30` | TTL en segundos para `/analytics/top-merchants` |

Las variables `E4_PARQUET` y `E4_DB` se configuran internamente en `docker-compose.yml`  
apuntando a los volúmenes montados; no es necesario modificarlas.

## Arquitectura de los servicios

```
docker compose up
│
├── setup  (corre una vez)
│     Lee:  /data/benchmark_1m/transactions.snappy.parquet  ← bind mount del host
│     Crea: /db/transactions.db                             ← volumen db_vol
│     Sale con código 0 al terminar
│
└── api    (arranca cuando setup completa con éxito)
      Lee:  /data/...parquet   (DuckDB — analytics)         ← mismo bind mount
      Lee:  /db/transactions.db (SQLite — transaccional)    ← mismo volumen db_vol
      Expone: 0.0.0.0:8000
```

## Estructura

```
ejercicio-07-contenedores/
├── Dockerfile          # multi-stage: builder (uv + deps) → runtime (app)
├── docker-compose.yml  # servicios setup + api, volúmenes compartidos
├── .env.example        # plantilla de variables de entorno
├── .dockerignore       # excluye datos, venv, __pycache__ del contexto
├── log_config.json     # formato JSON para uvicorn (1 objeto/línea a stdout)
└── README.md
```

El `log_formatter.py` que usa `log_config.json` vive en `ejercicio-04-sistema/`  
(es parte de la app del E4; uvicorn lo importa vía `--app-dir`).
