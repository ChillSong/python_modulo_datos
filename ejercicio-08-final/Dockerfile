# ── Stage 1: instalar dependencias ────────────────────────────────────────────
FROM python:3.12-slim AS builder

RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    rm -rf /venv/lib/python*/site-packages/pip* \
           /venv/lib/python*/site-packages/wheel* \
           /venv/lib/python*/site-packages/setuptools* \
           /venv/lib/python*/site-packages/pkg_resources* && \
    find /venv -name "*.so" -exec strip --strip-unneeded {} + && \
    find /venv -name "*.pyc" -delete && \
    find /venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

COPY --from=builder /venv /venv

# Codigo de aplicacion — sin datos (.parquet / .db se montan como volumenes)
COPY app/            /app/app/
COPY pipeline/       /app/pipeline/
COPY log_formatter.py /app/log_formatter.py
COPY log_config.json  /app/log_config.json
COPY schema.sql       /app/schema.sql
COPY setup_db.py      /app/setup_db.py

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PARQUET_PATH="/data/benchmark_1m/transactions.snappy.parquet" \
    DB_PATH="/db/transactions.db"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", \
     "--app-dir", "/app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--log-config", "/app/log_config.json"]
