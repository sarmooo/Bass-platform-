"""Per-tenant rate limiting (fixed-window).

The API gateway enforces a per-tenant request rate (docs/01-architecture.md). The
default `InMemoryRateLimiter` is process-local (fine for a single instance and
tests); `RedisRateLimiter` shares the window across instances via Redis atomics —
the production choice behind the same `RateLimiter` interface.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Callable, Protocol


class RateLimiter(Protocol):
    def allow(self, key: str) -> bool: ...


class InMemoryRateLimiter:
    """Fixed-window counter: at most `limit` calls per `window_s` per key."""

    def __init__(self, limit: int, window_s: float = 60.0,
                 clock: Callable[[], float] = time.time):
        self.limit = limit
        self.window_s = window_s
        self._clock = clock
        self._lock = threading.Lock()
        self._counts: dict[tuple[str, int], int] = defaultdict(int)

    def allow(self, key: str) -> bool:
        bucket = int(self._clock() // self.window_s)
        with self._lock:
            self._counts[(key, bucket)] += 1
            count = self._counts[(key, bucket)]
            # Opportunistically drop old buckets to bound memory.
            if len(self._counts) > 4096:
                self._counts = defaultdict(
                    int, {k: v for k, v in self._counts.items() if k[1] >= bucket})
        return count <= self.limit


class RedisRateLimiter:
    """Distributed fixed-window using Redis INCR + EXPIRE (atomic per window)."""

    def __init__(self, redis_client: Any, limit: int, window_s: int = 60,
                 clock: Callable[[], float] = time.time):
        self._redis = redis_client
        self.limit = limit
        self.window_s = window_s
        self._clock = clock

    def allow(self, key: str) -> bool:
        bucket = int(self._clock() // self.window_s)
        redis_key = f"rl:{key}:{bucket}"
        count = self._redis.incr(redis_key)
        if count == 1:
            self._redis.expire(redis_key, self.window_s)
        return int(count) <= self.limit
