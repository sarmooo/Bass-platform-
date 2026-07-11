"""The orchestrator: trigger → workflow → run.

Turns a trigger event into a durable run. Two responsibilities that matter for
correctness:
- **Deduplication.** A stable idempotency key is derived from the trigger (or
  supplied by the caller), so a redelivered webhook/email maps to the *same* run
  instead of starting a duplicate.
- **Resume.** `resume_run` re-hydrates a run from the store and drives it from
  its last committed cursor — the entry point a worker uses after a restart.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .agent import AgentRuntime
from .config import Config
from .models import Agent, Run, RunStatus, Tenant, Workflow
from .observability import Metrics, get_logger
from .policy import PolicyEngine
from .store import Store
from .tools import MockModel, ToolRegistry
from .workflow import ApprovalFn, WorkflowEngine

log = get_logger("orchestrator")


def derive_idempotency_key(trigger_event: dict) -> str:
    """Stable key from the trigger. A source-provided id (email Message-ID,
    webhook delivery id) is preferred; otherwise hash the canonical event."""
    for k in ("idempotency_key", "message_id", "delivery_id", "id"):
        if trigger_event.get(k):
            return str(trigger_event[k])
    blob = json.dumps(trigger_event, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(blob.encode()).hexdigest()[:24]


class Orchestrator:
    def __init__(self, store: Store, tenant: Tenant, agents: dict[str, Agent],
                 tools: ToolRegistry, policy: PolicyEngine,
                 approval_fn: ApprovalFn | None = None, model: Any = None,
                 config: Config | None = None, metrics: Metrics | None = None):
        self.store = store
        self.tenant = tenant
        self.workflows: dict[str, Workflow] = {}
        self.metrics = metrics or Metrics()
        self.config = config or Config()
        store.ensure_tenant(tenant.id, tenant.name, tenant.settings)

        model = model if model is not None else MockModel()
        self.runtime = AgentRuntime(model, tools, policy, metrics=self.metrics)
        self.engine = WorkflowEngine(store, agents, tools, policy, self.runtime,
                                     approval_fn=approval_fn, metrics=self.metrics)
        # Route the agent runtime's trace through the engine's buffered emitter.
        self.runtime._emit = self.engine.emit

    def register_workflow(self, wf: Workflow) -> None:
        self.workflows[wf.id] = wf

    def fire(self, wf: Workflow, trigger_event: dict) -> Run:
        """Handle a trigger: create-or-dedup a run, then drive it to completion."""
        self.register_workflow(wf)
        key = derive_idempotency_key(trigger_event)
        run = Run(tenant_id=self.tenant.id, workflow_id=wf.id, workflow_version=wf.version,
                  trigger_event=trigger_event, idempotency_key=key, cursor_step=wf.start)
        run, created = self.store.create_or_get_run(run)
        if not created:
            log.info("duplicate trigger ignored", run_id=run.id, key=key)
            self.metrics.incr("triggers.deduplicated")
            return run
        self.engine.emit(run, "trigger_fired", {"workflow": wf.name, "event": trigger_event})
        self.metrics.incr("triggers.accepted")
        return self.engine.resume(run, wf)

    def resume_run(self, run_id: str, wf: Workflow) -> Run:
        """Re-hydrate a run from the store and continue it (post-restart entry)."""
        run = self.store.load_run(self.tenant.id, run_id)
        if run is None:
            raise KeyError(run_id)
        if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED):
            return run
        return self.engine.resume(run, wf)

    def trace(self, run_id: str) -> list:
        return self.store.list_events(self.tenant.id, run_id)
