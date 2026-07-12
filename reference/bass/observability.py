"""Structured logging and in-process metrics.

Logs are JSON by default so they ship cleanly into a log pipeline; a trace-id is
bound per run and included on every line, so one run's activity is greppable
end to end. Metrics are simple thread-safe counters/observations — a production
deployment swaps `Metrics` for an OpenTelemetry meter without touching callers.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections import defaultdict
from contextvars import ContextVar
from typing import Any

# Bound per run so every log line and metric can be correlated to a run.
_trace_id: ContextVar[str] = ContextVar("trace_id", default="-")


def set_trace_id(trace_id: str) -> None:
    _trace_id.set(trace_id)


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": _trace_id.get(),
            "msg": record.getMessage(),
        }
        extra = getattr(record, "fields", None)
        if extra:
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", as_json: bool = True) -> None:
    root = logging.getLogger("bass")
    root.setLevel(level)
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_JsonFormatter() if as_json
                         else logging.Formatter("%(levelname)s %(name)s %(message)s"))
    root.addHandler(handler)
    root.propagate = False


def get_logger(name: str) -> "BoundLogger":
    return BoundLogger(logging.getLogger(f"bass.{name}"))


class BoundLogger:
    """Thin wrapper so structured fields are first-class: log.info(msg, k=v)."""

    def __init__(self, inner: logging.Logger):
        self._inner = inner

    def _log(self, level: int, msg: str, **fields: Any) -> None:
        self._inner.log(level, msg, extra={"fields": fields})

    def debug(self, msg: str, **f: Any) -> None: self._log(logging.DEBUG, msg, **f)
    def info(self, msg: str, **f: Any) -> None: self._log(logging.INFO, msg, **f)
    def warning(self, msg: str, **f: Any) -> None: self._log(logging.WARNING, msg, **f)
    def error(self, msg: str, **f: Any) -> None: self._log(logging.ERROR, msg, **f)


class Metrics:
    """Thread-safe counters and observations. Drop-in point for OpenTelemetry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple, float] = defaultdict(float)
        self._observations: dict[tuple, list[float]] = defaultdict(list)

    @staticmethod
    def _key(name: str, labels: dict[str, str] | None) -> tuple:
        return (name, tuple(sorted((labels or {}).items())))

    def incr(self, name: str, value: float = 1.0, **labels: str) -> None:
        with self._lock:
            self._counters[self._key(name, labels)] += value

    def observe(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self._observations[self._key(name, labels)].append(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "counters": {f"{n}{dict(l)}": v for (n, l), v in self._counters.items()},
                "observations": {f"{n}{dict(l)}": list(v)
                                 for (n, l), v in self._observations.items()},
            }
