"""Migration-runner tests.

The runner mechanics (ordering, idempotency, atomic rollback, ledger recording)
are dialect-agnostic and tested here against a temp SQLite database with
synthetic migrations. When BASS_TEST_DB_URL points at PostgreSQL (CI's
integration job), the real production migrations are applied and verified —
including that row-level security actually gets enabled.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from bass.errors import ConfigError
from bass.migrate import Migration, Migrator, run_migrations


def _tmp() -> str:
    return str(Path(tempfile.mkdtemp()) / "m.db")


_SYNTH = [
    Migration("0001_a", "create t1", "CREATE TABLE t1 (x INTEGER);"),
    Migration("0002_b", "create t2", "CREATE TABLE t2 (y INTEGER);"),
]


class RunnerMechanicsTests(unittest.TestCase):
    def test_applies_in_order_and_records(self):
        path = _tmp()
        applied = Migrator(path, migrations=_SYNTH).run()
        self.assertEqual(applied, ["0001_a", "0002_b"])
        conn = sqlite3.connect(path)
        recorded = [r[0] for r in conn.execute(
            "SELECT id FROM schema_migrations ORDER BY id").fetchall()]
        self.assertEqual(recorded, ["0001_a", "0002_b"])
        # The DDL actually ran.
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("t1", tables)
        self.assertIn("t2", tables)
        conn.close()

    def test_idempotent_second_run_applies_nothing(self):
        path = _tmp()
        Migrator(path, migrations=_SYNTH).run()
        again = Migrator(path, migrations=_SYNTH).run()
        self.assertEqual(again, [])                 # nothing pending

    def test_only_pending_are_applied(self):
        path = _tmp()
        Migrator(path, migrations=_SYNTH[:1]).run()  # apply just the first
        rest = Migrator(path, migrations=_SYNTH).run()
        self.assertEqual(rest, ["0002_b"])           # second only

    def test_failed_migration_rolls_back_and_stops(self):
        path = _tmp()
        bad = [
            Migration("0001_a", "ok", "CREATE TABLE t1 (x INTEGER);"),
            Migration("0002_bad", "broken", "CREATE TABLE t2 (this is not valid sql);"),
            Migration("0003_c", "never", "CREATE TABLE t3 (z INTEGER);"),
        ]
        with self.assertRaises(Exception):
            Migrator(path, migrations=bad).run()
        conn = sqlite3.connect(path)
        recorded = [r[0] for r in conn.execute(
            "SELECT id FROM schema_migrations").fetchall()]
        self.assertEqual(recorded, ["0001_a"])       # first committed, bad one did not
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertNotIn("t3", tables)               # later migration never ran
        conn.close()

    def test_run_migrations_rejects_non_postgres_url(self):
        with self.assertRaises(ConfigError):
            run_migrations("bass.db")                # sqlite store manages its own schema


@unittest.skipUnless(os.environ.get("BASS_TEST_DB_URL"), "BASS_TEST_DB_URL not set")
class PostgresMigrationTests(unittest.TestCase):
    """Applies the real production migrations to PostgreSQL and verifies them."""

    def setUp(self):
        self.url = os.environ["BASS_TEST_DB_URL"]
        import psycopg
        # Clean slate so the baseline actually applies.
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("DROP TABLE IF EXISTS schema_migrations, tool_effects, "
                         "approvals, events, runs, tenants CASCADE")

    def test_applies_baseline_and_rls_then_idempotent(self):
        applied = run_migrations(self.url)
        self.assertEqual(applied, ["0001_baseline_schema", "0002_row_level_security"])
        # Re-run is a no-op.
        self.assertEqual(run_migrations(self.url), [])

        import psycopg
        with psycopg.connect(self.url, autocommit=True) as conn:
            # The core tables exist.
            n = conn.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('runs','events','approvals','tool_effects')"
            ).fetchone()[0]
            self.assertEqual(n, 4)
            # RLS is actually enabled and a tenant_isolation policy exists per table.
            policies = conn.execute(
                "SELECT count(*) FROM pg_policies WHERE policyname='tenant_isolation'"
            ).fetchone()[0]
            self.assertEqual(policies, 4)


if __name__ == "__main__":
    unittest.main()
