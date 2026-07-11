"""PostgreSQL-backed `Store`.

Method-for-method parity with `SQLiteStore`, so the engine, orchestrator, and the
full test suite run against it unchanged — this is the production state layer.
Verified in CI against a real PostgreSQL service container (see
`.github/workflows/ci.yml`).

Connection is autocommit for single-statement reads; multi-statement writes are
wrapped in an explicit transaction so a run's checkpoint stays atomic. Enabling
PostgreSQL row-level security on top of the app-level tenant checks here is the
recommended defense-in-depth (one policy per table on
`tenant_id = current_setting('bass.tenant_id')`); it is left to a deployment
migration because it changes a cross-tenant read from "raise" to "empty".
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from .errors import TenantIsolationError
from .models import Event, Run, RunStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}', created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
    workflow_version INTEGER NOT NULL, trigger_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL, status TEXT NOT NULL, cursor_step TEXT,
    context_json TEXT NOT NULL, cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0,
    steps_used INTEGER NOT NULL DEFAULT 0, result_json TEXT,
    started_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
    UNIQUE (tenant_id, idempotency_key));
CREATE TABLE IF NOT EXISTS events (
    seq BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    step_id TEXT, type TEXT NOT NULL, actor TEXT NOT NULL, payload_json TEXT NOT NULL,
    cost_usd DOUBLE PRECISION NOT NULL DEFAULT 0, created_at DOUBLE PRECISION NOT NULL);
CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id, seq);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    step_id TEXT NOT NULL, status TEXT NOT NULL, approvers_json TEXT NOT NULL,
    decided_by TEXT, note TEXT, requested_at DOUBLE PRECISION NOT NULL,
    decided_at DOUBLE PRECISION);
CREATE TABLE IF NOT EXISTS tool_effects (
    idempotency_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    step_id TEXT, tool TEXT NOT NULL, result_json TEXT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL);
"""

# Row-level security: every tenant-scoped table is filtered by the session's
# bass.tenant_id GUC, so isolation is enforced by PostgreSQL even if application
# code is bypassed. FORCE makes the table owner subject to the policy too.
RLS_SQL = """
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['runs','events','approvals','tool_effects'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON %I '
      'USING (tenant_id = current_setting(''bass.tenant_id'', true)) '
      'WITH CHECK (tenant_id = current_setting(''bass.tenant_id'', true))', t);
  END LOOP;
END $$;
"""


class PostgresStore:
    def __init__(self, dsn: str):
        import psycopg                              # imported lazily
        from psycopg.rows import dict_row
        self._conn = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        with self._conn.transaction():
            self._conn.execute(_SCHEMA)
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    def apply_row_level_security(self) -> None:
        """Enable DB-enforced tenant isolation (defense in depth over the app
        checks). After this, the app must connect as a **non-superuser role
        without BYPASSRLS** (superusers and BYPASSRLS roles are never constrained
        by RLS) and set the tenant per transaction via
        ``SELECT set_config('bass.tenant_id', <tenant>, true)``. Provided as the
        production migration; not enabled by default so the reference store's
        behavior stays identical to SQLite."""
        with self._lock, self._conn.transaction():
            self._conn.execute(RLS_SQL)

    # -- tenants -----------------------------------------------------------

    def ensure_tenant(self, tenant_id: str, name: str, settings: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tenants (id, name, settings, created_at) VALUES (%s,%s,%s,%s) "
                "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name",
                (tenant_id, name, json.dumps(settings), time.time()))

    # -- runs --------------------------------------------------------------

    def create_or_get_run(self, run: Run) -> tuple[Run, bool]:
        now = time.time()
        with self._lock, self._conn.transaction():
            cur = self._conn.execute(
                "INSERT INTO runs (id, tenant_id, workflow_id, workflow_version, "
                "trigger_json, idempotency_key, status, cursor_step, context_json, "
                "cost_usd, steps_used, result_json, started_at, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING",
                (run.id, run.tenant_id, run.workflow_id, run.workflow_version,
                 json.dumps(run.trigger_event), run.idempotency_key, run.status.value,
                 run.cursor_step, json.dumps(run.context), run.cost_usd, run.steps_used,
                 None, now, now))
            created = cur.rowcount == 1
        if created:
            return run, True
        existing = self._load_by_key(run.tenant_id, run.idempotency_key)
        assert existing is not None
        return existing, False

    def load_run(self, tenant_id: str, run_id: str) -> Optional[Run]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id=%s", (run_id,)).fetchone()
        return self._row_to_run(row, tenant_id) if row else None

    def _load_by_key(self, tenant_id: str, key: str) -> Optional[Run]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE tenant_id=%s AND idempotency_key=%s",
                (tenant_id, key)).fetchone()
        return self._row_to_run(row, tenant_id) if row else None

    def checkpoint(self, run: Run, events: list[Event]) -> None:
        with self._lock, self._conn.transaction():
            self._assert_owns(run.tenant_id, run.id)
            self._conn.execute(
                "UPDATE runs SET status=%s, cursor_step=%s, context_json=%s, cost_usd=%s, "
                "steps_used=%s, result_json=%s, updated_at=%s WHERE id=%s AND tenant_id=%s",
                (run.status.value, run.cursor_step, json.dumps(run.context), run.cost_usd,
                 run.steps_used, json.dumps(run.result) if run.result is not None else None,
                 time.time(), run.id, run.tenant_id))
            from .redaction import redact
            for ev in events:
                if ev.tenant_id != run.tenant_id:
                    raise TenantIsolationError("event tenant does not match run tenant")
                self._conn.execute(
                    "INSERT INTO events (run_id, tenant_id, step_id, type, actor, "
                    "payload_json, cost_usd, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (ev.run_id, ev.tenant_id, ev.step_id, ev.type, ev.actor,
                     json.dumps(redact(ev.payload)), ev.cost_usd, time.time()))

    def list_events(self, tenant_id: str, run_id: str) -> list[Event]:
        with self._lock:
            self._assert_owns(tenant_id, run_id)
            rows = self._conn.execute(
                "SELECT * FROM events WHERE run_id=%s AND tenant_id=%s ORDER BY seq",
                (run_id, tenant_id)).fetchall()
        return [Event(run_id=r["run_id"], tenant_id=r["tenant_id"], step_id=r["step_id"],
                      type=r["type"], actor=r["actor"], payload=json.loads(r["payload_json"]),
                      cost_usd=r["cost_usd"], seq=r["seq"]) for r in rows]

    def cancel_run(self, tenant_id: str, run_id: str) -> bool:
        terminal = (RunStatus.SUCCEEDED.value, RunStatus.FAILED.value,
                    RunStatus.CANCELED.value)
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                "SELECT status FROM runs WHERE id=%s AND tenant_id=%s",
                (run_id, tenant_id)).fetchone()
            if row is None:
                self._assert_owns(tenant_id, run_id)
                return False
            if row["status"] in terminal:
                return False
            self._conn.execute(
                "UPDATE runs SET status=%s, updated_at=%s WHERE id=%s AND tenant_id=%s",
                (RunStatus.CANCELED.value, time.time(), run_id, tenant_id))
            self._conn.execute(
                "INSERT INTO events (run_id, tenant_id, step_id, type, actor, "
                "payload_json, cost_usd, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (run_id, tenant_id, None, "run_canceled", "user", "{}", 0, time.time()))
        return True

    # -- side-effect ledger ------------------------------------------------

    def get_effect(self, tenant_id: str, key: str) -> Optional[Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json, tenant_id FROM tool_effects WHERE idempotency_key=%s",
                (key,)).fetchone()
        if row is None:
            return None
        if row["tenant_id"] != tenant_id:
            raise TenantIsolationError("effect belongs to a different tenant")
        return json.loads(row["result_json"])

    def record_effect(self, tenant_id: str, key: str, run_id: str,
                      step_id: Optional[str], tool: str, result: Any) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO tool_effects (idempotency_key, run_id, tenant_id, step_id, "
                "tool, result_json, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (idempotency_key) DO NOTHING",
                (key, run_id, tenant_id, step_id, tool, json.dumps(result), time.time()))

    # -- approvals ---------------------------------------------------------

    def create_approval(self, tenant_id: str, run_id: str, step_id: str,
                        approvers: list[str]) -> str:
        from .models import new_id
        approval_id = new_id("appr")
        with self._lock, self._conn.transaction():
            self._assert_owns(tenant_id, run_id)
            self._conn.execute(
                "INSERT INTO approvals (id, run_id, tenant_id, step_id, status, "
                "approvers_json, requested_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (approval_id, run_id, tenant_id, step_id, "pending",
                 json.dumps(approvers), time.time()))
        return approval_id

    def resolve_approval(self, tenant_id: str, approval_id: str, approved: bool,
                         decided_by: str, note: str = "") -> None:
        with self._lock, self._conn.transaction():
            row = self._conn.execute(
                "SELECT tenant_id FROM approvals WHERE id=%s", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if row["tenant_id"] != tenant_id:
                raise TenantIsolationError("approval belongs to a different tenant")
            self._conn.execute(
                "UPDATE approvals SET status=%s, decided_by=%s, note=%s, decided_at=%s "
                "WHERE id=%s",
                ("approved" if approved else "rejected", decided_by, note, time.time(),
                 approval_id))

    def latest_approval(self, tenant_id: str, run_id: str,
                        step_id: str) -> Optional[tuple[str, str]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, status FROM approvals WHERE run_id=%s AND tenant_id=%s "
                "AND step_id=%s ORDER BY requested_at DESC LIMIT 1",
                (run_id, tenant_id, step_id)).fetchone()
        return (row["id"], row["status"]) if row else None

    # -- helpers -----------------------------------------------------------

    def _assert_owns(self, tenant_id: str, run_id: str) -> None:
        row = self._conn.execute(
            "SELECT tenant_id FROM runs WHERE id=%s", (run_id,)).fetchone()
        if row is not None and row["tenant_id"] != tenant_id:
            raise TenantIsolationError(f"run {run_id} belongs to a different tenant")

    @staticmethod
    def _row_to_run(row: dict, tenant_id: str) -> Run:
        if row["tenant_id"] != tenant_id:
            raise TenantIsolationError("run belongs to a different tenant")
        run = Run(
            tenant_id=row["tenant_id"], workflow_id=row["workflow_id"],
            workflow_version=row["workflow_version"],
            trigger_event=json.loads(row["trigger_json"]),
            idempotency_key=row["idempotency_key"], status=RunStatus(row["status"]),
            cursor_step=row["cursor_step"], context=json.loads(row["context_json"]),
            cost_usd=row["cost_usd"], steps_used=row["steps_used"],
            result=json.loads(row["result_json"]) if row["result_json"] else None)
        run.id = row["id"]
        return run
