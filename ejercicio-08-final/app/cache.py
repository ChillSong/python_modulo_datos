"""Cache en memoria con TTL configurable por entrada y metricas globales.

Identico al del E4 — la implementacion fue validada en produccion (benchmark
E4: summary cold=45.6ms, warm=0.6ms, ratio 76x).
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
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is not None and entry.expires_at > now:
                self._hits += 1
                return True, entry.value
            if entry is not None:
                del self._store[key]
            self._misses += 1
            return False, None

    def set(self, key: str, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        with self._lock:
            self._store[key] = _Entry(value, time.monotonic() + ttl)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._hits = 0
            self._misses = 0

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
