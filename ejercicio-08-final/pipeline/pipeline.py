"""Orquestador del pipeline ETL: extract -> transform -> load.

Fuente: archivo CSV externo con el schema fijo del modulo.
Destino: SQLite (tabla transactions, misma estructura que E3/E4).

Idempotente: INSERT OR IGNORE por transaction_id garantiza que procesar
el mismo CSV dos veces deja la base en el mismo estado.

Uso:
    python -m pipeline.pipeline --csv /data/input.csv
    python -m pipeline.pipeline --csv /data/input.csv --db /db/transactions.db
    python -m pipeline.pipeline --csv /data/input.csv --quarantine /tmp/q --results /tmp/r
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

from .extract import extract, read_csv
from .load import load
from .transform import transform

HERE = Path(__file__).resolve().parent.parent  # ejercicio-08-final/
DEFAULT_DB = HERE / "db" / "transactions.db"
DEFAULT_QUARANTINE = HERE / "quarantine"
DEFAULT_RESULTS = HERE / "results"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Pipeline ETL CSV -> SQLite (E8).")
    p.add_argument("--csv", type=Path, required=True,
                   help="CSV de entrada con el schema fijo del modulo.")
    p.add_argument("--db", type=Path, default=DEFAULT_DB,
                   help=f"SQLite destino. Default: {DEFAULT_DB}.")
    p.add_argument("--quarantine", type=Path, default=DEFAULT_QUARANTINE,
                   help=f"Directorio de cuarentena. Default: {DEFAULT_QUARANTINE}.")
    p.add_argument("--results", type=Path, default=DEFAULT_RESULTS,
                   help=f"Directorio de reportes. Default: {DEFAULT_RESULTS}.")
    return p.parse_args()


def run(
    *,
    csv_path: Path,
    db_path: Path = DEFAULT_DB,
    quarantine_dir: Path = DEFAULT_QUARANTINE,
    results_dir: Path = DEFAULT_RESULTS,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now()
    t_start = time.perf_counter()

    raw_rows = read_csv(csv_path)
    extracted = extract(raw_rows)
    valid, rejected_counts = transform(extracted, quarantine_dir=quarantine_dir, now=now)
    load_stats = load(valid, db_path)

    elapsed = time.perf_counter() - t_start
    run_id = now.strftime("%Y%m%d_%H%M%S")

    report = {
        "run_id": run_id,
        "started_at": now.isoformat(timespec="seconds"),
        "total_seconds": round(elapsed, 4),
        "source": {"type": "csv", "path": str(csv_path)},
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


def _print_summary(r: dict) -> None:
    print(f"Run {r['run_id']}  ({r['total_seconds']:.3f}s)")
    print(f"  fuente      : {r['source']['path']}")
    print(f"  extraidas   : {r['extracted']}")
    print(f"  validas     : {r['valid']}")
    print(f"  rechazadas  : {r['rejected']['total']}")
    for reason, n in r["rejected"]["by_reason"].items():
        print(f"      - {reason:<22} {n}")
    print(f"  insertadas  : {r['loaded']['inserted']}")
    print(f"  duplicadas  : {r['loaded']['duplicates_skipped']}")
    print(f"  reporte     : {r['report_file']}")


def main() -> None:
    args = _parse_args()
    if not args.csv.exists():
        raise FileNotFoundError(f"CSV no encontrado: {args.csv}")
    report = run(
        csv_path=args.csv,
        db_path=args.db,
        quarantine_dir=args.quarantine,
        results_dir=args.results,
    )
    _print_summary(report)


if __name__ == "__main__":
    main()
