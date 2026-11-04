"""Caché exact-match de respuestas del LLM.

Si llega exactamente la misma petición (mismo proveedor, modelo, prompt y mensajes), se
devuelve la respuesta guardada sin llamar al proveedor: cero coste y latencia mínima.
Vive en memoria del proceso, así que se pierde al reiniciar y no se comparte entre réplicas.
"""

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


class ExactMatchCache:
    """Caché LRU con caducidad (TTL)."""

    def __init__(
        self,
        ttl_seconds: float,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(**request: Any) -> str:
        """Clave: sha256 del JSON canónico de la petición (claves ordenadas)."""
        canonical = json.dumps(request, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode()).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self._entries.get(key)
        if entry is None or self._clock() >= entry[0]:
            self._entries.pop(key, None)
            self.misses += 1
            return None
        self._entries.move_to_end(key)
        self.hits += 1
        return dict(entry[1])

    def set(self, key: str, value: dict[str, Any]) -> None:
        self._entries[key] = (self._clock() + self.ttl_seconds, dict(value))
        self._entries.move_to_end(key)
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)

    def __len__(self) -> int:
        return len(self._entries)
