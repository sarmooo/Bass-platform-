"""System runner — resume a run to completion without a tenant-facing caller.

Used by out-of-band drivers (the Temporal worker, a queue consumer) that hold a
run id and need to advance it. Builds a fresh engine over the shared store and
resumes; the store remains the durable source of truth.
"""

from __future__ import annotations

import os
from typing import Any

from .agent import AgentRuntime
from .models import Run
from .policy import PolicyEngine
from .store import Store
from .tools import ToolRegistry
from .workflow import WorkflowEngine


class SystemRunner:
    def __init__(self, store: Store, agents: dict, tools: ToolRegistry,
                 policy: PolicyEngine, workflows: dict, model: Any = None):
        from .tools import MockModel
        self.store = store
        self.agents = agents
        self.tools = tools
        self.policy = policy
        self.workflows = workflows                 # by workflow id
        self.model = model or MockModel()

    def resume(self, tenant_id: str, run_id: str) -> Run:
        run = self.store.load_run(tenant_id, run_id)
        if run is None:
            raise KeyError(run_id)
        wf = self.workflows[run.workflow_id]
        runtime = AgentRuntime(self.model, self.tools, self.policy)
        engine = WorkflowEngine(self.store, self.agents, self.tools, self.policy,
                                runtime, approval_fn=None)
        runtime._emit = engine.emit
        return engine.resume(run, wf)


def build_from_env() -> tuple[SystemRunner, Any]:
    """Assemble a SystemRunner + the reference workflow from the environment."""
    from .example_run import build_agents, build_workflow
    from .policy import default_policies
    from .store import open_store
    from .tools import default_registry
    store = open_store(os.environ.get("BASS_DB_PATH", "bass.db"))
    wf = build_workflow()
    runner = SystemRunner(store, build_agents(), default_registry(),
                          PolicyEngine(default_policies()), {wf.id: wf})
    return runner, wf
