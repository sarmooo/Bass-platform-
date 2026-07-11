"""PostgreSQL row-level security — DB-enforced tenant isolation.

Proves that the RLS policy the platform ships (FORCE ROW LEVEL SECURITY +
`tenant_id = current_setting('bass.tenant_id')`) actually filters cross-tenant
reads at the database, even on a direct SQL query that bypasses the application's
own tenant checks. PostgreSQL-only: skipped unless BASS_TEST_DB_URL is a Postgres
DSN (set in the integration-postgres CI job).
"""

from __future__ import annotations

import os
import unittest

_URL = os.environ.get("BASS_TEST_DB_URL", "")
_IS_PG = _URL.startswith(("postgres://", "postgresql://"))


@unittest.skipUnless(_IS_PG, "requires a PostgreSQL BASS_TEST_DB_URL")
class RlsTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        self.conn = psycopg.connect(_URL, autocommit=True)
        c = self.conn
        c.execute("DROP TABLE IF EXISTS rls_demo")
        c.execute("CREATE TABLE rls_demo (id text primary key, tenant_id text not null)")
        c.execute("ALTER TABLE rls_demo ENABLE ROW LEVEL SECURITY")
        c.execute("ALTER TABLE rls_demo FORCE ROW LEVEL SECURITY")
        c.execute("DROP POLICY IF EXISTS tenant_isolation ON rls_demo")
        c.execute(
            "CREATE POLICY tenant_isolation ON rls_demo "
            "USING (tenant_id = current_setting('bass.tenant_id', true)) "
            "WITH CHECK (tenant_id = current_setting('bass.tenant_id', true))")

    def tearDown(self):
        self.conn.execute("DROP TABLE IF EXISTS rls_demo")
        self.conn.close()

    def _set_tenant(self, tenant: str) -> None:
        self.conn.execute("SELECT set_config('bass.tenant_id', %s, true)", (tenant,))

    def test_rls_isolates_tenants_at_the_database(self):
        for tenant, rid in [("tenant_a", "r1"), ("tenant_b", "r2")]:
            with self.conn.transaction():
                self._set_tenant(tenant)
                self.conn.execute("INSERT INTO rls_demo (id, tenant_id) VALUES (%s, %s)",
                                  (rid, tenant))

        # As tenant_a, a full scan sees only tenant_a's row.
        with self.conn.transaction():
            self._set_tenant("tenant_a")
            ids = [r[0] for r in self.conn.execute("SELECT id FROM rls_demo").fetchall()]
        self.assertEqual(ids, ["r1"])

        # Even a targeted read of tenant_b's row returns nothing — the database,
        # not the application, enforces the boundary.
        with self.conn.transaction():
            self._set_tenant("tenant_a")
            row = self.conn.execute("SELECT id FROM rls_demo WHERE id = 'r2'").fetchone()
        self.assertIsNone(row)

    def test_write_mislabeled_for_another_tenant_is_rejected(self):
        import psycopg
        with self.assertRaises(psycopg.errors.Error):
            with self.conn.transaction():
                self._set_tenant("tenant_a")
                self.conn.execute(
                    "INSERT INTO rls_demo (id, tenant_id) VALUES ('r3', 'tenant_b')")


if __name__ == "__main__":
    unittest.main()
