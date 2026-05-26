"""Management command: carga el Parquet del E1 en la base via el ORM.

    uv run python manage.py load_transactions
    uv run python manage.py load_transactions --parquet ../data/benchmark_1m/transactions.snappy.parquet --chunk-size 5000

Lee el Parquet en memoria, construye objetos `Transaction` y los inserta por
chunks con `bulk_create(..., ignore_conflicts=True)`. Idempotente: volver a
correrlo con los mismos datos NO duplica filas (la PK `transaction_id` ignora
los conflictos). El `--truncate` vacia la tabla antes de cargar.
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from transactions.models import Transaction

_FIELDS = [
    "transaction_id", "timestamp", "user_id", "merchant_id",
    "amount", "category", "country_code", "status",
]


class Command(BaseCommand):
    help = "Carga transacciones desde el Parquet del E1 usando el ORM (idempotente)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--parquet",
            default=settings.E5_PARQUET,
            help="Ruta del Parquet (default: settings.E5_PARQUET).",
        )
        parser.add_argument(
            "--chunk-size", type=int, default=5000,
            help="Filas por lote de bulk_create (default 5000).",
        )
        parser.add_argument(
            "--truncate", action="store_true",
            help="Vacia la tabla antes de cargar.",
        )

    def handle(self, *args, **opts):
        parquet = Path(opts["parquet"])
        chunk_size = opts["chunk_size"]
        if not parquet.exists():
            raise CommandError(
                f"Parquet no encontrado: {parquet}. Genera el dataset del E1 (ver README)."
            )

        if opts["truncate"]:
            deleted, _ = Transaction.objects.all().delete()
            self.stdout.write(f"Tabla vaciada ({deleted} filas).")

        self.stdout.write(f"Leyendo {parquet} ...")
        df = pd.read_parquet(parquet, columns=_FIELDS)
        # Timestamps de pandas -> datetime nativo (USE_TZ=False guarda naive).
        df["timestamp"] = df["timestamp"].dt.to_pydatetime()
        total = len(df)
        self.stdout.write(f"{total:,} filas en el Parquet. Insertando en chunks de {chunk_size} ...")

        records = df.to_dict("records")
        inserted = 0
        t0 = time.perf_counter()
        for start in range(0, total, chunk_size):
            batch = [Transaction(**r) for r in records[start : start + chunk_size]]
            created = Transaction.objects.bulk_create(
                batch, ignore_conflicts=True, batch_size=chunk_size
            )
            inserted += len(created)
        elapsed = time.perf_counter() - t0

        final = Transaction.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Listo en {elapsed:.2f}s. Filas en la base: {final:,} "
                f"(procesadas {total:,}; nuevas en esta corrida segun bulk_create: {inserted:,})."
            )
        )
