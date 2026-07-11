"""Versioned schema migrations for the PostgreSQL state layer.

The `SQLiteStore` creates its schema on connect (a dev convenience). Production
runs against PostgreSQL, where schema changes must be **explicit, ordered, and
auditable** — that is what this runner provides:

- an ordered list of `Migration`s, each with a stable `id` (the version);
- a `schema_migrations` ledger recording what has been applied;
- transactional application — each migration's DDL and its ledger row commit
  together, so a failure leaves the database on the last good version;
- idempotency — re-running applies only what is pending (safe to run on every
  deploy).

The baseline migration is the core schema; the second enables per-tenant
row-level security. Run it with `bass-migrate postgresql://…` (or set
`BASS_DB_URL`). The runner mechanics are dialect-agnostic and unit-tested against
SQLite; the real PostgreSQL migrations run in CI's integration job.
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass
from typing import Optional, Sequence

from .errors import ConfigError
from .observability import get_logger
from .store_postgres import RLS_SQL, _SCHEMA

log = get_logger("migrate")

_MIGRATIONS_TABLE = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "id TEXT PRIMARY KEY, applied_at DOUBLE PRECISION NOT NULL)")


@dataclass(frozen=True)
class Migration:
    id: str                 # ordered version, e.g. "0001_baseline_schema"
    description: str
    sql: str


# Production PostgreSQL migrations, applied in listed order.
POSTGRES_MIGRATIONS: list[Migration] = [
    Migration("0001_baseline_schema", "core tables, indexes and constraints", _SCHEMA),
    Migration("0002_row_level_security", "per-tenant row-level security policies", RLS_SQL),
]


class Migrator:
    """Applies pending migrations to a database, tracked in `schema_migrations`."""

    def __init__(self, url: str, migrations: Optional[Sequence[Migration]] = None):
        if url.startswith(("postgres://", "postgresql://")):
            self.dialect = "postgres"
            self.ph = "%s"
        elif url.startswith("sqlite://") or migrations is not None:
            # SQLite is supported only for testing the runner mechanics; the
            # production migrations target PostgreSQL.
            self.dialect = "sqlite"
            self.ph = "?"
        else:
            raise ConfigError(
                "migrations target PostgreSQL; the SQLite store manages its own "
                f"schema. Got url: {url!r}")
        self.url = url
        self._override = list(migrations) if migrations is not None else None

    def _connect(self):
        if self.dialect == "postgres":
            import psycopg
            return psycopg.connect(self.url, autocommit=False)
        import sqlite3
        path = self.url[len("sqlite://"):] if self.url.startswith("sqlite://") else self.url
        return sqlite3.connect(path)

    def _exec_ddl(self, conn, sql: str) -> None:
        # Both drivers accept multi-statement DDL, via different calls.
        if self.dialect == "postgres":
            conn.execute(sql)                    # psycopg runs multiple statements w/o params
        else:
            conn.executescript(sql)              # sqlite3 needs executescript for many

    def migrations(self) -> list[Migration]:
        if self._override is not None:
            return self._override
        return POSTGRES_MIGRATIONS

    def applied(self, conn) -> set[str]:
        self._exec_ddl(conn, _MIGRATIONS_TABLE)
        conn.commit()
        rows = conn.execute("SELECT id FROM schema_migrations").fetchall()
        return {r[0] for r in rows}

    def run(self) -> list[str]:
        """Apply pending migrations in order; return the ids newly applied."""
        conn = self._connect()
        try:
            done = self.applied(conn)
            newly: list[str] = []
            for m in self.migrations():
                if m.id in done:
                    continue
                try:
                    self._exec_ddl(conn, m.sql)
                    conn.execute(
                        f"INSERT INTO schema_migrations (id, applied_at) "
                        f"VALUES ({self.ph}, {self.ph})", (m.id, time.time()))
                    conn.commit()               # DDL + ledger row commit together
                except Exception:
                    conn.rollback()             # leave DB on the last good version
                    log.error("migration failed", migration=m.id)
                    raise
                log.info("migration applied", migration=m.id, description=m.description)
                newly.append(m.id)
            return newly
        finally:
            conn.close()


def run_migrations(url: str) -> list[str]:
    """Apply all pending PostgreSQL migrations at `url`. Returns applied ids."""
    return Migrator(url).run()


def main(argv: Optional[list[str]] = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    url = args[0] if args else os.environ.get("BASS_DB_URL", "")
    if not url:
        print("usage: bass-migrate <postgres-url>   (or set BASS_DB_URL)", file=sys.stderr)
        return 2
    applied = run_migrations(url)
    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("database is up to date; nothing to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
