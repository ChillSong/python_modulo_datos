# python_modulo_datos

Modulo: **Python para Sistemas de Datos Modernos** (Storage · Query · Pipelines · Serving).

Repositorio único con los 4 ejercicios del modulo:

```
python_modulo_datos/
├── ejercicio-01-formatos/   # Benchmark de formatos de almacenamiento
├── ejercicio-02-consultas/  # Motor de consultas (pandas / DuckDB / polars)
├── ejercicio-03-sqlite/     # Capa transaccional con SQLite
├── ejercicio-04-sistema/    # API FastAPI con backends duales y caching
├── data/                    # Datasets generados (NO se sube al repo)
├── pyproject.toml
└── README.md
```

## Setup

Requisitos: **Python 3.12+** y **uv**.

```bash
# 1. Instalar uv (si no lo tienes)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. Clonar el repo
git clone <URL_DEL_REPO>
cd python_modulo_datos

# 3. Crear el entorno e instalar dependencias
uv sync
```

`uv sync` lee `pyproject.toml` + `uv.lock` y arma el entorno virtual en `.venv/` con versiones exactas.

## Cómo correr cada ejercicio

Cada carpeta `ejercicio-XX-*/` tiene su propio README con los comandos puntuales. En general:

```bash
uv run python ejercicio-01-formatos/benchmark_cli.py --size 1m --formats csv parquet_gzip
uv run pytest ejercicio-04-sistema/tests/
```

## Notas importantes

- Los archivos de datos (`.csv`, `.parquet`, `.db`) **no se versionan**. La carpeta `data/` está en `.gitignore`.
- Para regenerar cualquier dataset, mira el README del ejercicio correspondiente.
