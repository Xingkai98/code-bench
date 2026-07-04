"""TTL cache template."""


class TTLCache:
    def __init__(self, capacity, ttl_seconds, clock=None):
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def set(self, key, value):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError
