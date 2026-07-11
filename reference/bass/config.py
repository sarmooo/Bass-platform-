"""Runtime configuration, resolved from the environment.

Every knob has a safe default so the service boots without a config file, but any
value can be overridden via ``BASS_*`` environment variables. Configuration is
read once at startup and passed explicitly — never read from os.environ deep in
the call stack.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .errors import ConfigError


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class Config:
    # Persistence. `db_path` is a SQLite path in the reference deployment; a
    # production deployment points the Store interface at Postgres instead.
    db_path: str = "bass.db"

    # Per-run safety ceilings (a workflow may set tighter ones).
    default_budget_usd: float = 1.0
    default_max_steps: int = 50

    # Connector reliability defaults.
    connector_timeout_s: float = 10.0
    connector_max_retries: int = 3
    connector_breaker_threshold: int = 5      # consecutive failures before open
    connector_breaker_reset_s: float = 30.0

    # Observability.
    log_level: str = "INFO"
    log_json: bool = True

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            db_path=os.environ.get("BASS_DB_PATH", cls.db_path),
            default_budget_usd=_float("BASS_DEFAULT_BUDGET_USD", cls.default_budget_usd),
            default_max_steps=_int("BASS_DEFAULT_MAX_STEPS", cls.default_max_steps),
            connector_timeout_s=_float("BASS_CONNECTOR_TIMEOUT_S", cls.connector_timeout_s),
            connector_max_retries=_int("BASS_CONNECTOR_MAX_RETRIES", cls.connector_max_retries),
            connector_breaker_threshold=_int(
                "BASS_CONNECTOR_BREAKER_THRESHOLD", cls.connector_breaker_threshold),
            connector_breaker_reset_s=_float(
                "BASS_CONNECTOR_BREAKER_RESET_S", cls.connector_breaker_reset_s),
            log_level=os.environ.get("BASS_LOG_LEVEL", cls.log_level),
            log_json=os.environ.get("BASS_LOG_JSON", "1") != "0",
        )
