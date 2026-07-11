"""Connector reliability primitives.

A connector is the authenticated client a tool uses to reach an external system.
Every call goes through `Connector.run`, which enforces the production essentials:
a hard **timeout**, **bounded retries** with exponential backoff on transient
failures, and a **circuit breaker** so a flaky dependency degrades one tool
rather than stalling the whole platform.

Concrete connectors (Gmail, Salesforce, an HTTP endpoint, a database) subclass or
compose `Connector`; the reference ships an in-process example in `tools.py`.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from typing import Any, Callable

from .errors import CircuitOpen, ConnectorError
from .observability import get_logger

log = get_logger("connector")


class Transient(ConnectorError):
    """A retryable failure (timeout, 5xx, connection reset)."""


@dataclass
class ReliabilityConfig:
    timeout_s: float = 10.0
    max_retries: int = 3
    backoff_base_s: float = 0.05
    backoff_cap_s: float = 2.0
    breaker_threshold: int = 5
    breaker_reset_s: float = 30.0


class CircuitBreaker:
    """Trips open after N consecutive failures; half-opens after a cooldown."""

    def __init__(self, threshold: int, reset_s: float, now: Callable[[], float] = time.monotonic):
        self._threshold = threshold
        self._reset_s = reset_s
        self._now = now
        self._failures = 0
        self._opened_at: float | None = None

    def allow(self) -> bool:
        if self._opened_at is None:
            return True
        if self._now() - self._opened_at >= self._reset_s:
            return True                          # half-open: allow one trial
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._now()

    @property
    def is_open(self) -> bool:
        return self._opened_at is not None and self._now() - self._opened_at < self._reset_s


class Connector:
    """Wraps operations with timeout + retry + circuit breaking."""

    def __init__(self, name: str, config: ReliabilityConfig | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.name = name
        self.config = config or ReliabilityConfig()
        self._breaker = CircuitBreaker(self.config.breaker_threshold,
                                       self.config.breaker_reset_s)
        self._sleep = sleep
        self._pool = ThreadPoolExecutor(max_workers=4)

    def run(self, fn: Callable[[dict], Any], args: dict) -> Any:
        cfg = self.config
        if not self._breaker.allow():
            raise CircuitOpen(f"connector '{self.name}' circuit is open")

        last: Exception | None = None
        for attempt in range(cfg.max_retries + 1):
            try:
                future = self._pool.submit(fn, args)
                result = future.result(timeout=cfg.timeout_s)
                self._breaker.record_success()
                return result
            except FutureTimeout:
                last = Transient(f"connector '{self.name}' timed out after {cfg.timeout_s}s")
                self._breaker.record_failure()
            except Transient as exc:
                last = exc
                self._breaker.record_failure()
            except ConnectorError:
                raise                            # non-transient: do not retry
            except Exception as exc:             # unknown error from the operation
                last = ConnectorError(f"connector '{self.name}' failed: {exc}")
                self._breaker.record_failure()
                break                            # only retry explicitly transient errors

            if attempt < cfg.max_retries:
                delay = min(cfg.backoff_base_s * (2 ** attempt), cfg.backoff_cap_s)
                log.warning("connector retry", connector=self.name,
                            attempt=attempt + 1, delay_s=delay)
                self._sleep(delay)

        assert last is not None
        raise last
