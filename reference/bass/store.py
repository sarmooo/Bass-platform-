"""Durable persistence.

`Store` is the interface the engine depends on; `SQLiteStore` is the reference
implementation. A production deployment provides a Postgres-backed `Store` with
row-level security — the engine is unchanged because it only sees this interface.

Guarantees the reference store provides:
- **ACID checkpoints.** A run's committed state (status, cursor, context, cost,
  and its new events) advances in a single transaction, so a crash never leaves
  a run half-advanced.
- **Trigger idempotency.** `(tenant_id, idempotency_key)` is unique — the same
  real-world event maps to the same run instead of spawning a duplicate.
- **Side-effect idempotency.** `record_effect`/`get_effect` back a ledger keyed
  by a deterministic idempotency key, so a mutating tool replayed after a crash
  returns the recorded result instead of acting twice.
- **Tenant isolation.** Every read and write is scoped by `tenant_id`; a mismatch
  raises `TenantIsolationError` rather than silently crossing the boundary.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Optional, Protocol

from .errors import TenantIsolationError
from .models import Event, Run, RunStatus
from .redaction import redact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tenants (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    settings TEXT NOT NULL DEFAULT '{}', created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
    workflow_version INTEGER NOT NULL, trigger_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL, status TEXT NOT NULL, cursor_step TEXT,
    context_json TEXT NOT NULL, cost_usd REAL NOT NULL DEFAULT 0,
    steps_used INTEGER NOT NULL DEFAULT 0, result_json TEXT,
    started_at REAL NOT NULL, updated_at REAL NOT NULL,
    UNIQUE (tenant_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL, step_id TEXT, type TEXT NOT NULL, actor TEXT NOT NULL,
    payload_json TEXT NOT NULL, cost_usd REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_run ON events (run_id, seq);
CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    step_id TEXT NOT NULL, status TEXT NOT NULL, approvers_json TEXT NOT NULL,
    decided_by TEXT, note TEXT, requested_at REAL NOT NULL, decided_at REAL
);
-- Append-only ledger of externally-visible side effects, for exactly-once.
CREATE TABLE IF NOT EXISTS tool_effects (
    idempotency_key TEXT PRIMARY KEY, run_id TEXT NOT NULL, tenant_id TEXT NOT NULL,
    step_id TEXT, tool TEXT NOT NULL, result_json TEXT NOT NULL, created_at REAL NOT NULL
);
"""


def open_store(url_or_path: str) -> "Store":
    """Store factory. A PostgreSQL DSN selects `PostgresStore`; anything else is
    treated as a SQLite path. Lets a deployment switch the state layer with one
    env var and no code change."""
    if url_or_path.startswith(("postgres://", "postgresql://")):
        from .store_postgres import PostgresStore
        return PostgresStore(url_or_path)
    return SQLiteStore(url_or_path)


class Store(Protocol):
    def ensure_tenant(self, tenant_id: str, name: str, settings: dict) -> None: ...
    def create_or_get_run(self, run: Run) -> tuple[Run, bool]: ...
    def load_run(self, tenant_id: str, run_id: str) -> Optional[Run]: ...
    def checkpoint(self, run: Run, events: list[Event]) -> None: ...
    def list_events(self, tenant_id: str, run_id: str) -> list[Event]: ...
    def get_effect(self, tenant_id: str, key: str) -> Optional[Any]: ...
    def record_effect(self, tenant_id: str, key: str, run_id: str,
                      step_id: Optional[str], tool: str, result: Any) -> None: ...
    def create_approval(self, tenant_id: str, run_id: str, step_id: str,
                        approvers: list[str]) -> str: ...
    def resolve_approval(self, tenant_id: str, approval_id: str, approved: bool,
                         decided_by: str, note: str = "") -> None: ...


class SQLiteStore:
    """Reference `Store`. Single connection guarded by a lock (one process)."""

    def __init__(self, path: str = "bass.db"):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._lock = threading.Lock()

    def close(self) -> None:
        self._conn.close()

    # -- tenants -----------------------------------------------------------

    def ensure_tenant(self, tenant_id: str, name: str, settings: dict) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO tenants (id, name, settings, created_at) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                (tenant_id, name, json.dumps(settings), time.time()),
            )

    # -- runs --------------------------------------------------------------

    def create_or_get_run(self, run: Run) -> tuple[Run, bool]:
        """Insert a run, or return the existing one for a duplicate trigger.

        Returns (run, created). `created=False` means the idempotency key was
        already seen — the caller must not start a second execution.
        """
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO runs (id, tenant_id, workflow_id, "
                "workflow_version, trigger_json, idempotency_key, status, cursor_step, "
                "context_json, cost_usd, steps_used, result_json, started_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (run.id, run.tenant_id, run.workflow_id, run.workflow_version,
                 json.dumps(run.trigger_event), run.idempotency_key, run.status.value,
                 run.cursor_step, json.dumps(run.context), run.cost_usd, run.steps_used,
                 None, now, now),
            )
            if cur.rowcount == 1:
                return run, True
        existing = self._load_run_by_key(run.tenant_id, run.idempotency_key)
        assert existing is not None
        return existing, False

    def load_run(self, tenant_id: str, run_id: str) -> Optional[Run]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._row_to_run(row, tenant_id) if row else None

    def _load_run_by_key(self, tenant_id: str, key: str) -> Optional[Run]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE tenant_id=? AND idempotency_key=?",
                (tenant_id, key)).fetchone()
        return self._row_to_run(row, tenant_id) if row else None

    def checkpoint(self, run: Run, events: list[Event]) -> None:
        """Atomically persist run state and append its new events."""
        with self._lock, self._conn:
            self._assert_owns(run.tenant_id, run.id)
            self._conn.execute(
                "UPDATE runs SET status=?, cursor_step=?, context_json=?, cost_usd=?, "
                "steps_used=?, result_json=?, updated_at=? WHERE id=? AND tenant_id=?",
                (run.status.value, run.cursor_step, json.dumps(run.context),
                 run.cost_usd, run.steps_used,
                 json.dumps(run.result) if run.result is not None else None,
                 time.time(), run.id, run.tenant_id),
            )
            for ev in events:
                if ev.tenant_id != run.tenant_id:
                    raise TenantIsolationError("event tenant does not match run tenant")
                self._conn.execute(
                    "INSERT INTO events (run_id, tenant_id, step_id, type, actor, "
                    "payload_json, cost_usd, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (ev.run_id, ev.tenant_id, ev.step_id, ev.type, ev.actor,
                     json.dumps(redact(ev.payload)), ev.cost_usd, time.time()),
                )

    def list_events(self, tenant_id: str, run_id: str) -> list[Event]:
        with self._lock:
            self._assert_owns(tenant_id, run_id)
            rows = self._conn.execute(
                "SELECT * FROM events WHERE run_id=? AND tenant_id=? ORDER BY seq",
                (run_id, tenant_id)).fetchall()
        return [Event(run_id=r["run_id"], tenant_id=r["tenant_id"], step_id=r["step_id"],
                      type=r["type"], actor=r["actor"], payload=json.loads(r["payload_json"]),
                      cost_usd=r["cost_usd"], seq=r["seq"]) for r in rows]

    # -- side-effect ledger (exactly-once) ---------------------------------

    def get_effect(self, tenant_id: str, key: str) -> Optional[Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT result_json, tenant_id FROM tool_effects WHERE idempotency_key=?",
                (key,)).fetchone()
        if row is None:
            return None
        if row["tenant_id"] != tenant_id:
            raise TenantIsolationError("effect belongs to a different tenant")
        return json.loads(row["result_json"])

    def record_effect(self, tenant_id: str, key: str, run_id: str,
                      step_id: Optional[str], tool: str, result: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO tool_effects (idempotency_key, run_id, tenant_id, "
                "step_id, tool, result_json, created_at) VALUES (?,?,?,?,?,?,?)",
                (key, run_id, tenant_id, step_id, tool, json.dumps(result), time.time()),
            )

    # -- approvals ---------------------------------------------------------

    def create_approval(self, tenant_id: str, run_id: str, step_id: str,
                        approvers: list[str]) -> str:
        from .models import new_id
        approval_id = new_id("appr")
        with self._lock, self._conn:
            self._assert_owns(tenant_id, run_id)
            self._conn.execute(
                "INSERT INTO approvals (id, run_id, tenant_id, step_id, status, "
                "approvers_json, requested_at) VALUES (?,?,?,?,?,?,?)",
                (approval_id, run_id, tenant_id, step_id,
                 ApprovalStatusValue.PENDING, json.dumps(approvers), time.time()),
            )
        return approval_id

    def resolve_approval(self, tenant_id: str, approval_id: str, approved: bool,
                         decided_by: str, note: str = "") -> None:
        status = ApprovalStatusValue.APPROVED if approved else ApprovalStatusValue.REJECTED
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT tenant_id FROM approvals WHERE id=?", (approval_id,)).fetchone()
            if row is None:
                raise KeyError(approval_id)
            if row["tenant_id"] != tenant_id:
                raise TenantIsolationError("approval belongs to a different tenant")
            self._conn.execute(
                "UPDATE approvals SET status=?, decided_by=?, note=?, decided_at=? WHERE id=?",
                (status, decided_by, note, time.time(), approval_id),
            )

    # -- helpers -----------------------------------------------------------

    def _assert_owns(self, tenant_id: str, run_id: str) -> None:
        row = self._conn.execute(
            "SELECT tenant_id FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is not None and row["tenant_id"] != tenant_id:
            raise TenantIsolationError(f"run {run_id} belongs to a different tenant")

    @staticmethod
    def _row_to_run(row: sqlite3.Row, tenant_id: str) -> Run:
        if row["tenant_id"] != tenant_id:
            raise TenantIsolationError("run belongs to a different tenant")
        run = Run(
            tenant_id=row["tenant_id"], workflow_id=row["workflow_id"],
            workflow_version=row["workflow_version"],
            trigger_event=json.loads(row["trigger_json"]),
            idempotency_key=row["idempotency_key"], status=RunStatus(row["status"]),
            cursor_step=row["cursor_step"], context=json.loads(row["context_json"]),
            cost_usd=row["cost_usd"], steps_used=row["steps_used"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
        )
        run.id = row["id"]
        return run


# ApprovalStatus values as plain strings, avoiding an import cycle at module load.
class ApprovalStatusValue:
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
