"""Minimal in-memory stand-in for the redis-py calls the import pipeline makes."""

from __future__ import annotations


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}
        self.ttl: dict[str, int] = {}

    def get(self, key: str) -> str | None:
        return self.data.get(key)

    def set(self, key: str, value, ex: int | None = None, nx: bool = False):
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        if ex is not None:
            self.ttl[key] = ex
        else:
            self.ttl.pop(key, None)
        return True

    def incr(self, key: str) -> int:
        value = int(self.data.get(key, 0)) + 1
        self.data[key] = str(value)
        return value

    def expire(self, key: str, seconds: int) -> bool:
        if key not in self.data:
            return False
        self.ttl[key] = seconds
        return True

    def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            if self.data.pop(key, None) is not None:
                removed += 1
            self.ttl.pop(key, None)
        return removed

    def pipeline(self) -> "_Pipeline":
        return _Pipeline(self)


class _Pipeline:
    def __init__(self, redis: FakeRedis) -> None:
        self._redis = redis
        self._ops: list = []

    def __getattr__(self, name: str):
        def queue(*args, **kwargs):
            self._ops.append((name, args, kwargs))
            return self
        return queue

    def execute(self) -> list:
        results = [getattr(self._redis, name)(*args, **kwargs) for name, args, kwargs in self._ops]
        self._ops = []
        return results
