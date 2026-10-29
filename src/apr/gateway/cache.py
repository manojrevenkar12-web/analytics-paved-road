"""Results keyed by the physical query, which names the exact Iceberg snapshots it reads.
A new snapshot of any input changes the key, so the cache can never return a result older than
the data; nothing has to be invalidated. Least recently used results go first."""

from __future__ import annotations

import threading
from collections import OrderedDict

import pyarrow as pa


class ResultCache:
    def __init__(self, max_bytes: int):
        self.max_bytes = max_bytes
        self.bytes = 0
        self._d: OrderedDict[str, pa.Table] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> pa.Table | None:
        with self._lock:
            t = self._d.get(key)
            if t is None:
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return t

    def put(self, key: str, table: pa.Table) -> None:
        size = table.nbytes
        if size > self.max_bytes:
            return
        with self._lock:
            if key in self._d:
                return
            self._d[key] = table
            self.bytes += size
            while self.bytes > self.max_bytes:
                _, old = self._d.popitem(last=False)
                self.bytes -= old.nbytes

    def __len__(self) -> int:
        return len(self._d)
