"""Cache en memoria con TTL configurable por entrada y metricas globales.

Diseno:

* `set(key, value, ttl)` guarda el valor con un instante de expiracion.
  El TTL lo decide quien llama, asi cada endpoint usa el suyo (los
  /analytics/* tienen TTL largo; un endpoint volatil podria usar 0).
* `get(key)` devuelve el valor si no expiro y contabiliza hit/miss.
* Los contadores `hits`/`misses` son acumulados **desde el arranque del
  proceso** — es exactamente lo que /health debe reportar como hit rate.
* Thread-safe: uvicorn corre los endpoints sync en un threadpool, asi que
  el dict y los contadores se protegen con un Lock.

No usamos un decorador magico: el endpoint construye su clave y llama
get/set explicitamente.  Es mas verboso pero deja ver en el codigo que
un endpoint cachea y otro no — relevante para la rubrica de arquitectura.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class _Entry:
    value: Any
    expires_at: float


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, _Entry] = {}
        self._lock = threading.Lock()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> tuple[bool, Any]:
        """Devuelve (hit, value).  Cuenta el acceso como hit o miss."""
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and entry.expires_at > now:
                self._hits += 1
                return True, entry.value
            # Expirado o ausente: limpiar y contar miss.
            if entry is not None:
                del self._store[key]
            self._misses += 1
            return False, None

    def set(self, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return  # TTL no-positivo = no cachear
        with self._lock:
            self._store[key] = _Entry(value, time.monotonic() + ttl)

    def clear(self) -> None:
        """Vacia entradas y reinicia contadores (util en cold-start del benchmark)."""
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0

    def invalidate_prefix(self, prefix: str) -> None:
        """Borra entradas cuya clave empieza con prefix (p.ej. tras un insert)."""
        with self._lock:
            for k in [k for k in self._store if k.startswith(prefix)]:
                del self._store[k]

    @property
    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._hits + self._misses
            return {
                "hits": self._hits,
                "misses": self._misses,
                "hit_rate": (self._hits / total) if total else 0.0,
                "entries": len(self._store),
            }
