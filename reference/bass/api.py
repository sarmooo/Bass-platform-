"""HTTP API — the service surface.

A FastAPI application over the engine: fire triggers, read runs and their traces,
and resolve approvals. Every request runs under a `Principal` decoded from a
bearer JWT; the principal's `tenant_id` scopes all data access, and RBAC roles
gate mutating endpoints. Approvals are asynchronous — firing a run that needs
approval returns it in `waiting_approval`, and the approval endpoint resolves it
and resumes the run.

Requires the `[api]` extra (fastapi, uvicorn, pyjwt, httpx). The core package
does not import this module, so installing the API stack is optional.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from .agent import AgentRuntime
from .auth import AuthError, Principal, decode_token
from .errors import BassError, TenantIsolationError
from .models import Agent, Run, Workflow
from .observability import Metrics
from .orchestrator import derive_idempotency_key
from .policy import PolicyEngine
from .store import Store
from .tools import MockModel, ToolRegistry
from .workflow import WorkflowEngine


class ApiService:
    """Framework-agnostic service the HTTP layer calls into."""

    def __init__(self, store: Store, agents: dict[str, Agent], tools: ToolRegistry,
                 policy: PolicyEngine, jwt_secret: str, model: Any = None,
                 metrics: Metrics | None = None):
        self.store = store
        self.agents = agents
        self.tools = tools
        self.policy = policy
        self.jwt_secret = jwt_secret
        self.model = model or MockModel()
        self.metrics = metrics or Metrics()
        self.workflows_by_id: dict[str, Workflow] = {}
        self.workflows_by_name: dict[str, Workflow] = {}

    def register_workflow(self, wf: Workflow) -> None:
        self.workflows_by_id[wf.id] = wf
        self.workflows_by_name[wf.name] = wf

    def _engine(self) -> WorkflowEngine:
        # Fresh engine per request (its event buffer is per-run); approval_fn=None
        # selects asynchronous approvals.
        runtime = AgentRuntime(self.model, self.tools, self.policy, metrics=self.metrics)
        engine = WorkflowEngine(self.store, self.agents, self.tools, self.policy,
                                runtime, approval_fn=None, metrics=self.metrics)
        runtime._emit = engine.emit
        return engine

    def _safe_resume(self, engine: WorkflowEngine, run: Run, wf: Workflow) -> Run:
        try:
            return engine.resume(run, wf)
        except BassError:
            # resume marks the run FAILED and checkpoints before raising; reload.
            reloaded = self.store.load_run(run.tenant_id, run.id)
            return reloaded if reloaded is not None else run

    def fire(self, tenant_id: str, wf_name: str, trigger: dict) -> Run:
        wf = self.workflows_by_name.get(wf_name)
        if wf is None:
            raise KeyError(wf_name)
        self.store.ensure_tenant(tenant_id, tenant_id, {})
        run = Run(tenant_id=tenant_id, workflow_id=wf.id, workflow_version=wf.version,
                  trigger_event=trigger, idempotency_key=derive_idempotency_key(trigger),
                  cursor_step=wf.start)
        engine = self._engine()
        run, created = self.store.create_or_get_run(run)
        if created:
            engine.emit(run, "trigger_fired", {"workflow": wf.name, "event": trigger})
            run = self._safe_resume(engine, run, wf)
        return run

    def get_run(self, tenant_id: str, run_id: str) -> Optional[Run]:
        return self.store.load_run(tenant_id, run_id)

    def trace(self, tenant_id: str, run_id: str) -> list:
        return self.store.list_events(tenant_id, run_id)

    def cancel(self, tenant_id: str, run_id: str) -> Run:
        self.store.cancel_run(tenant_id, run_id)
        run = self.store.load_run(tenant_id, run_id)
        if run is None:
            raise KeyError(run_id)
        return run

    def resolve_approval(self, tenant_id: str, run_id: str, approval_id: str,
                         approved: bool, decided_by: str, note: str = "") -> Run:
        run = self.store.load_run(tenant_id, run_id)          # tenant-scoped
        if run is None:
            raise KeyError(run_id)
        self.store.resolve_approval(tenant_id, approval_id, approved, decided_by, note)
        wf = self.workflows_by_id[run.workflow_id]
        return self._safe_resume(self._engine(), run, wf)


# --- view helpers -----------------------------------------------------------

def _run_view(run: Run) -> dict:
    return {"run_id": run.id, "status": run.status.value, "cost_usd": run.cost_usd,
            "result": run.result}


def _event_view(ev: Any) -> dict:
    return {"seq": ev.seq, "type": ev.type, "step_id": ev.step_id, "payload": ev.payload}


# --- FastAPI app ------------------------------------------------------------

def create_app(service: ApiService):
    from fastapi import Depends, FastAPI, Header, HTTPException

    app = FastAPI(title="Bass API", version="0.1.0")

    def principal(authorization: str = Header(default="")) -> Principal:
        if not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="missing bearer token")
        try:
            return decode_token(service.jwt_secret, authorization[len("Bearer "):])
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc))

    def require(*roles: str):
        def dep(p: Principal = Depends(principal)) -> Principal:
            if roles and not p.has_any(*roles):
                raise HTTPException(status_code=403,
                                    detail=f"requires one of roles: {list(roles)}")
            return p
        return dep

    @app.get("/healthz")
    def health() -> dict:
        return {"status": "ok"}

    @app.post("/v1/workflows/{name}/runs")
    def fire(name: str, body: dict,
             p: Principal = Depends(require("owner", "admin", "builder"))) -> dict:
        try:
            run = service.fire(p.tenant_id, name, body.get("trigger", {}))
        except KeyError:
            raise HTTPException(status_code=404, detail="unknown workflow")
        return _run_view(run)

    @app.get("/v1/runs/{run_id}")
    def get_run(run_id: str, p: Principal = Depends(require())) -> dict:
        try:
            run = service.get_run(p.tenant_id, run_id)
        except TenantIsolationError:
            raise HTTPException(status_code=404, detail="not found")
        if run is None:
            raise HTTPException(status_code=404, detail="not found")
        return _run_view(run)

    @app.get("/v1/runs/{run_id}/trace")
    def get_trace(run_id: str, p: Principal = Depends(require())) -> dict:
        try:
            events = service.trace(p.tenant_id, run_id)
        except TenantIsolationError:
            raise HTTPException(status_code=404, detail="not found")
        return {"events": [_event_view(e) for e in events]}

    @app.post("/v1/runs/{run_id}/cancel")
    def cancel(run_id: str,
               p: Principal = Depends(require("owner", "admin", "builder"))) -> dict:
        try:
            run = service.cancel(p.tenant_id, run_id)
        except (KeyError, TenantIsolationError):
            raise HTTPException(status_code=404, detail="not found")
        return _run_view(run)

    @app.post("/v1/runs/{run_id}/approvals/{approval_id}")
    def resolve(run_id: str, approval_id: str, body: dict,
                p: Principal = Depends(require("owner", "admin", "approver"))) -> dict:
        try:
            run = service.resolve_approval(
                p.tenant_id, run_id, approval_id, bool(body.get("approved", False)),
                decided_by=p.subject, note=body.get("note", ""))
        except (KeyError, TenantIsolationError):
            raise HTTPException(status_code=404, detail="not found")
        return _run_view(run)

    return app


def build_default_app():
    """Convenience factory wiring the reference workflow, for `uvicorn`/manual use."""
    from .example_run import build_agents, build_workflow
    from .policy import default_policies
    from .store import open_store
    from .tools import default_registry

    secret = os.environ.get("BASS_JWT_SECRET")
    if not secret:
        raise BassError("BASS_JWT_SECRET must be set to run the API")
    store = open_store(os.environ.get("BASS_DB_PATH", "bass.db"))
    service = ApiService(store, build_agents(), default_registry(),
                         PolicyEngine(default_policies()), jwt_secret=secret)
    service.register_workflow(build_workflow())
    return create_app(service)
