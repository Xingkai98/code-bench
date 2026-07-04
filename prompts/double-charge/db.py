"""
Simulated database backed by a JSON file.

Uses time.sleep() to simulate realistic I/O latency (network + disk).
Each individual read/write is protected by a file lock, so single operations
are atomic. However, the application-level pattern of read-then-write across
separate calls is NOT atomic — the lock is released between calls.
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Optional


class Database:
    """Simple JSON-file key-value store.

    Each "table" is a top-level key in the JSON object::

        {
          "products":  {"p1": {...}, "p2": {...}},
          "orders":    {"ord-1": {...}},
          "payments":  {"pay-1": {...}},
          "coupons":   {"FIRST50": {...}}
        }

    Individual read/write calls hold an internal file lock during I/O.
    """

    def __init__(self, filepath: str = "store.json"):
        self.filepath = filepath
        self._io_lock = threading.Lock()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not os.path.exists(self.filepath):
            with open(self.filepath, "w") as f:
                json.dump({}, f)

    def _load(self) -> dict:
        time.sleep(0.00003)
        with self._io_lock:
            with open(self.filepath, "r") as f:
                return json.load(f)

    def _save(self, data: dict) -> None:
        time.sleep(0.00005)
        with self._io_lock:
            with open(self.filepath, "w") as f:
                json.dump(data, f, indent=2)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def read(self, table: str, key: str) -> Optional[dict]:
        """Read a single record from a table. Returns None if missing."""
        data = self._load()
        return data.get(table, {}).get(key)

    def write(self, table: str, key: str, value: dict) -> None:
        """Write (upsert) a single record into a table."""
        data = self._load()
        data.setdefault(table, {})[key] = value
        self._save(data)

    def delete(self, table: str, key: str) -> None:
        """Delete a record from a table. No-op if missing."""
        data = self._load()
        if table in data and key in data[table]:
            del data[table][key]
            self._save(data)

    def list_all(self, table: str) -> list[dict]:
        """Return all records in a table as a list of dicts."""
        data = self._load()
        return list(data.get(table, {}).values())
