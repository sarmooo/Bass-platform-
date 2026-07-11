"""Secret resolution.

Connectors fetch credentials through a `SecretsProvider`; the raw value never
enters a model context, an event payload, or a log line (see `redaction.py`).
`EnvSecrets` reads from the process environment for the reference deployment; a
production deployment implements this interface against Vault or a cloud KMS.
"""

from __future__ import annotations

import os
from typing import Protocol

from .errors import ConfigError


class SecretsProvider(Protocol):
    def get(self, ref: str) -> str: ...


class EnvSecrets:
    """Resolve `secret_ref` values from environment variables (BASS_SECRET_<REF>)."""

    def get(self, ref: str) -> str:
        key = f"BASS_SECRET_{ref.upper()}"
        val = os.environ.get(key)
        if val is None:
            raise ConfigError(f"secret {ref!r} not configured ({key} unset)")
        return val


class StaticSecrets:
    """In-memory provider for tests and local development."""

    def __init__(self, values: dict[str, str] | None = None):
        self._values = dict(values or {})

    def get(self, ref: str) -> str:
        if ref not in self._values:
            raise ConfigError(f"secret {ref!r} not configured")
        return self._values[ref]
