"""Orquestador del pipeline ETL: extract -> transform -> load.

Genera un reporte de ejecucion por corrida en `results/run_YYYYMMDD_HHMMSS.json`
con todas las metricas que pide el PDF: filas extraidas, validas, rechazadas
por tipo de error, insertadas, duplicadas y tiempo total.

Idempotencia: la fuente simulada es deterministica en (size, error_rate, seed)
y la carga usa INSERT OR IGNORE por transaction_id; correr este script dos
veces con los mismos argumentos deja la base en el mismo estado y la segunda
corrida reporta `inserted=0`, `duplicates_skipped=N`.

Uso:
    python pipeline.py
    python pipeline.py --batch-size 1000 --error-rate 0.2 --seed 7
    python pipeline.py --source-file inbox/batch.jsonl
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from data_source import simulate_batch
from extract import extract
from load import load
from transform import transform

HERE = Path(__file__).resolve().parent
DEFAULT_DB = HERE / "db" / "transactions.db"
QUARANTINE_DIR = HERE / "quarantine"
RESULTS_DIR = HERE / "results"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pipeline ETL del E6.")
    p.add_argument("--batch-size", type=int, default=500,
                   help="Tamano del batch simulado (100..1000). Default 500.")
    p.add_argument("--error-rate", type=float, default=0.1,
                   help="Fraccion de errores inyectados (0.0..1.0). Default 0.1.")
    p.add_argument("--seed", type=int, default=42,
                   help="Semilla del generador deterministico. Default 42.")
    p.add_argument("--source-file", type=Path, default=None,
                   help="Si se pasa, lee el batch desde un JSONL en vez de simularlo.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB,
                   help=f"Path de la base SQLite destino. Default {DEFAULT_DB}.")
    return p.parse_args()


def _load_from_file(path: Path) -> list[dict]:
    with path.open() as fh:
        return [json.loads(line) for line in fh if line.strip()]


def run(
    *,
    batch_size: int = 500,
    error_rate: float = 0.1,
    seed: int = 42,
    source_file: Path | None = None,
    db_path: Path = DEFAULT_DB,
    quarantine_dir: Path = QUARANTINE_DIR,
    results_dir: Path = RESULTS_DIR,
    now: datetime | None = None,
) -> dict:
    """Ejecuta una corrida completa y devuelve el reporte (ademas de escribirlo)."""
    now = now or datetime.now()
    t_start = time.perf_counter()

    # --- Fuente -----------------------------------------------------------
    if source_file is not None:
        raw_batch = _load_from_file(source_file)
        source_meta = {"type": "file", "path": str(source_file)}
    else:
        raw_batch = simulate_batch(batch_size, error_rate, seed, now=now)
        source_meta = {
            "type": "simulated",
            "batch_size": batch_size,
            "error_rate": error_rate,
            "seed": seed,
        }

    # --- Extract ----------------------------------------------------------
    extracted = extract(raw_batch)

    # --- Transform + cuarentena ------------------------------------------
    valid, rejected_counts = transform(
        extracted, quarantine_dir=quarantine_dir, now=now,
    )

    # --- Load -------------------------------------------------------------
    load_stats = load(valid, db_path)

    elapsed = time.perf_counter() - t_start

    # --- Reporte de ejecucion --------------------------------------------
    run_id = now.strftime("%Y%m%d_%H%M%S")
    report = {
        "run_id": run_id,
        "started_at": now.isoformat(timespec="seconds"),
        "total_seconds": round(elapsed, 4),
        "source": source_meta,
        "extracted": len(extracted),
        "valid": len(valid),
        "rejected": {
            "total": sum(rejected_counts.values()),
            "by_reason": dict(sorted(rejected_counts.items())),
        },
        "loaded": load_stats,
        "db": str(db_path),
        "quarantine_file": str(quarantine_dir / f"{now.strftime('%Y-%m-%d')}.jsonl"),
    }

    results_dir.mkdir(parents=True, exist_ok=True)
    out_path = results_dir / f"run_{run_id}.json"
    out_path.write_text(json.dumps(report, indent=2))
    report["report_file"] = str(out_path)
    return report


def _print_summary(report: dict) -> None:
    print(f"Run {report['run_id']}  ({report['total_seconds']:.3f}s)")
    print(f"  source      : {report['source']}")
    print(f"  extracted   : {report['extracted']}")
    print(f"  valid       : {report['valid']}")
    print(f"  rejected    : {report['rejected']['total']}")
    for reason, n in report["rejected"]["by_reason"].items():
        print(f"      - {reason:<22} {n}")
    print(f"  inserted    : {report['loaded']['inserted']}")
    print(f"  duplicates  : {report['loaded']['duplicates_skipped']}")
    print(f"  db          : {report['db']}")
    print(f"  report      : {report['report_file']}")


def main() -> None:
    args = _parse_args()
    report = run(
        batch_size=args.batch_size,
        error_rate=args.error_rate,
        seed=args.seed,
        source_file=args.source_file,
        db_path=args.db,
    )
    _print_summary(report)


if __name__ == "__main__":
    main()
