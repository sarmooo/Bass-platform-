"""The orchestrator ties triggers -> workflows -> runs.

In production this owns idempotency/dedup, queuing, and durable resumption. Here it
is a thin coordinator that wires the pieces together and creates a Run from a trigger
event.
"""

from __future__ import annotations

from typing import Callable

from .agent import AgentRuntime
from .models import Agent, Run, Tenant, Workflow
from .policy import PolicyEngine
from .tools import MockModel, ToolRegistry
from .workflow import WorkflowEngine


class Orchestrator:
    def __init__(self, tenant: Tenant, agents: dict[str, Agent], tools: ToolRegistry,
                 policy: PolicyEngine, approval_fn: Callable | None = None,
                 model=None):
        self.tenant = tenant
        # Default to the offline MockModel; pass providers.AnthropicModel(tools)
        # (or any object with a compatible .respond) to run against the real API.
        model = model if model is not None else MockModel()
        self.runtime = AgentRuntime(model, tools, policy, approval_fn=approval_fn)
        self.engine = WorkflowEngine(agents, tools, policy, self.runtime)

    def fire(self, workflow: Workflow, trigger_event: dict) -> Run:
        """Simulate a trigger firing: create a run and execute it end to end."""
        run = Run(
            workflow_id=workflow.id,
            tenant_id=self.tenant.id,
            trigger_event=trigger_event,
        )
        run.log("trigger_fired", {"workflow": workflow.name, "event": trigger_event})
        return self.engine.execute(workflow, run)
