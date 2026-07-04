"""
Intentionally incomplete TTL + LRU cache implementation.

This is kept outside prompts/ for eval-design comparison only. It should not be
copied into model workspaces.
"""
import time


class TTLCache:
    def __init__(self, capacity, ttl_seconds, clock=None):
        self.capacity = capacity
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.time
        self._data = {}
        self._order = []

    def _is_expired(self, expires_at):
        # Bug: exact expiry time is treated as still valid.
        return self.clock() > expires_at

    def get(self, key):
        item = self._data.get(key)
        if item is None:
            return None

        value, expires_at = item
        if self._is_expired(expires_at):
            self._data.pop(key, None)
            if key in self._order:
                self._order.remove(key)
            return None

        # Bug: a successful get should refresh LRU order.
        return value

    def set(self, key, value):
        if self.capacity <= 0:
            return

        expires_at = self.clock() + self.ttl_seconds
        self._data[key] = (value, expires_at)

        # Bug: overwriting an existing key should move it to most-recently-used.
        if key not in self._order:
            self._order.append(key)

        # Bug: expired keys should be purged before capacity eviction.
        while len(self._data) > self.capacity and self._order:
            oldest = self._order.pop(0)
            self._data.pop(oldest, None)

    def delete(self, key):
        self._data.pop(key, None)
        if key in self._order:
            self._order.remove(key)

    def __len__(self):
        return len(self._data)
