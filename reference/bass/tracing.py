"""OpenTelemetry tracing — a thin, optional wrapper.

`span(name, **attrs)` is a context manager the engine uses to emit spans for runs
and steps. If `opentelemetry` isn't installed it degrades to a no-op, so the core
package keeps zero required dependencies; install the `[otel]` extra to activate
it, and wire a real exporter/collector in your deployment. Attributes are
namespaced under `bass.*`.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

try:
    from opentelemetry import trace as _ot
    _HAVE = True
except ImportError:                              # pragma: no cover - optional dep
    _HAVE = False


def tracing_enabled() -> bool:
    return _HAVE


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    if not _HAVE:
        yield None
        return
    tracer = _ot.get_tracer("bass")
    with tracer.start_as_current_span(name) as sp:
        for key, value in attrs.items():
            if value is not None:
                sp.set_attribute(f"bass.{key}", value)
        yield sp
