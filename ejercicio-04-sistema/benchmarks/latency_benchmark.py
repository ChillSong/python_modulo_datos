"""Benchmark de latencia de la API del E4.

Mide 100 requests por endpoint y reporta p50/p95/p99.  Para los endpoints
analiticos compara dos regimenes:

  * cold  — el cache se vacia antes de CADA request, asi que cada una paga
            el escaneo completo en DuckDB (peor caso, sin cache).
  * warm  — el cache se calienta una vez y las 100 requests son hits.

La diferencia cold/warm es exactamente el valor del cache, que es lo que el
PDF pide demostrar con numeros reales.

Se corre in-process con TestClient para poder controlar el estado del cache
entre requests (vaciarlo en cada iteracion cold).  Por eso las latencias NO
incluyen el salto de red: son una cota inferior del costo real, pero el
*ratio* cold/warm — el efecto que se estudia — es fiel.  Los SLAs del PDF
estan pensados sobre un servidor con red, asi que los comparamos como
referencia, no como veredicto de produccion.

Uso:
    uv run python ejercicio-04-sistema/benchmarks/latency_benchmark.py
    uv run python ejercicio-04-sistema/benchmarks/latency_benchmark.py --reps 100
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent  # ejercicio-04-sistema/
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.db import DEFAULT_DB  # noqa: E402
from app.main import app  # noqa: E402

# SLA del PDF por endpoint (ms).  Para analytics: (cold, warm).
SLA = {
    "/analytics/summary": {"cold": 500, "warm": 20},
    "/analytics/top-merchants": {"cold": 500, "warm": 20},
    "/users/{id}/transactions": {"single": 80},
    "/users/{id}/stats": {"single": 80},
    "/health": {"single": 50},
}


def percentiles(samples_ms: list[float]) -> dict[str, float]:
    s = sorted(samples_ms)
    q = statistics.quantiles(s, n=100)  # q[k-1] ~ percentil k
    return {
        "mean": round(statistics.mean(s), 3),
        "p50": round(q[49], 3),
        "p95": round(q[94], 3),
        "p99": round(q[98], 3),
        "max": round(s[-1], 3),
    }


def measure(fn, reps: int) -> list[float]:
    out = []
    for _ in range(reps):
        t0 = time.perf_counter()
        fn()
        out.append((time.perf_counter() - t0) * 1000)
    return out


def bench_analytics(client: TestClient, path: str, reps: int) -> dict:
    cache = client.app.state.cache

    # cold: vaciar el cache antes de cada request.
    def cold_call():
        cache.clear()
        r = client.get(path)
        assert r.status_code == 200

    cold = measure(cold_call, reps)

    # warm: calentar una vez y pegarle al cache.
    cache.clear()
    client.get(path)
    warm = measure(lambda: client.get(path), reps)

    return {"cold": percentiles(cold), "warm": percentiles(warm)}


def bench_single(client: TestClient, path: str, reps: int) -> dict:
    def call():
        r = client.get(path)
        assert r.status_code == 200

    return {"single": percentiles(measure(call, reps))}


def main() -> None:
    ap = argparse.ArgumentParser(description="Benchmark de latencia E4.")
    ap.add_argument("--reps", type=int, default=100)
    ap.add_argument("--output", type=Path, default=ROOT / "results" / "latency.json")
    args = ap.parse_args()

    if not DEFAULT_DB.exists():
        raise FileNotFoundError(
            f"Falta {DEFAULT_DB}. Corre: uv run python ejercicio-04-sistema/setup_db.py"
        )

    results: dict[str, dict] = {}
    with TestClient(app) as client:
        print(f"Benchmark con {args.reps} requests por endpoint/regimen...\n")

        for path in ("/analytics/summary", "/analytics/top-merchants?limit=10"):
            label = path.split("?")[0]
            res = bench_analytics(client, path, args.reps)
            results[label] = res
            print(f"{label}")
            print(f"  cold  p50={res['cold']['p50']:.2f}ms  p95={res['cold']['p95']:.2f}ms  "
                  f"p99={res['cold']['p99']:.2f}ms")
            print(f"  warm  p50={res['warm']['p50']:.3f}ms  p95={res['warm']['p95']:.3f}ms  "
                  f"p99={res['warm']['p99']:.3f}ms")
            speedup = res["cold"]["p50"] / max(res["warm"]["p50"], 1e-6)
            print(f"  speedup p50 (cold/warm): {speedup:,.0f}x\n")

        for path, label in (
            ("/users/1/transactions?page=1&page_size=20", "/users/{id}/transactions"),
            ("/users/1/stats", "/users/{id}/stats"),
            ("/health", "/health"),
        ):
            res = bench_single(client, path, args.reps)
            results[label] = res
            print(f"{label}")
            print(f"  p50={res['single']['p50']:.3f}ms  p95={res['single']['p95']:.3f}ms  "
                  f"p99={res['single']['p99']:.3f}ms\n")

    payload = {
        "reps": args.reps,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "note": "in-process TestClient; latencias sin salto de red, ratio cold/warm fiel",
        "sla_ms": SLA,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2))
    print(f"Resultados -> {args.output}")


if __name__ == "__main__":
    main()
