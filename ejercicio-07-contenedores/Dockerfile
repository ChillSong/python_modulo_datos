# ── Stage 1: install dependencies ─────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Create an isolated venv so the runtime stage can copy just that directory.
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Only the E4 runtime deps (fastapi + uvicorn + duckdb).
# DuckDB reads Parquet natively, so pyarrow/pandas are not needed.
# This keeps the final image well under 300 MB.
COPY ejercicio-07-contenedores/requirements-e4.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && \
    # Remove pip, wheel, setuptools — not needed at runtime
    rm -rf /venv/lib/python*/site-packages/pip* \
           /venv/lib/python*/site-packages/wheel* \
           /venv/lib/python*/site-packages/setuptools* \
           /venv/lib/python*/site-packages/pkg_resources* && \
    # Strip compiled extensions to reduce binary size
    find /venv -name "*.so" -exec strip --strip-unneeded {} + && \
    # Remove .pyc cache files
    find /venv -name "*.pyc" -delete && \
    find /venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime
WORKDIR /app

# Copy the pre-built venv — no pip or build tools in the final image
COPY --from=builder /venv /venv

# Copy application code — no data files (.parquet / .db) are included
COPY ejercicio-04-sistema/ /app/ejercicio-04-sistema/
COPY ejercicio-07-contenedores/log_config.json /app/log_config.json

ENV PATH="/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    E4_PARQUET="/data/benchmark_1m/transactions.snappy.parquet" \
    E4_DB="/db/transactions.db"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["uvicorn", "app.main:app", \
     "--app-dir", "/app/ejercicio-04-sistema", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--log-config", "/app/log_config.json"]
