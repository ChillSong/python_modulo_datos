"""Formateador JSON para uvicorn — un objeto JSON por linea en stdout.

Importable via --app-dir /app cuando el Dockerfile copia este archivo
al mismo nivel que el paquete app/.
"""

import json
import logging


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps({
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "message": record.getMessage(),
        })
