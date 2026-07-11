"""Core domain types.

These mirror the entities in docs/03-data-model.md, trimmed to what the runnable
example needs. Kept as plain dataclasses so the shapes are obvious.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


# --- identifiers ------------------------------------------------------------

# Deterministic, monotonic ids so example output is stable and easy to read.
_counter = itertools.count(1)


def new_id(prefix: str) -> str:
    return f"{prefix}_{next(_counter)}"


# --- enums ------------------------------------------------------------------

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


# --- tenancy ----------------------------------------------------------------

@dataclass
class Tenant:
    name: str
    id: str = field(default_factory=lambda: new_id("tenant"))
    settings: dict[str, Any] = field(default_factory=dict)


# --- tools & agents ---------------------------------------------------------

@dataclass
class Tool:
    """A single capability exposed to an agent, backed by a connector.

    `side_effect=True` marks a mutating tool (subject to stricter policy).
    `fn` is the connector call; in production it would hit an external system.
    """
    name: str
    description: str
    fn: Callable[[dict], Any]
    side_effect: bool = False
    cost_usd: float = 0.0


@dataclass
class Agent:
    """An LLM-driven worker: a role + instructions + a tool allow-list + guardrails."""
    name: str
    role: str
    instructions: str
    model: str = "claude-sonnet-5"
    tools: list[str] = field(default_factory=list)   # allow-list of tool names
    max_steps: int = 8


# --- workflows --------------------------------------------------------------

@dataclass
class Step:
    """One node in a workflow.

    node_type: 'agent' | 'tool' | 'branch' | 'approval'
    Fields used depend on node_type — see workflow.py for how each is executed.
    """
    id: str
    node_type: str
    # agent node
    agent: Optional[str] = None
    # tool node
    tool: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    # branch node: list of (predicate, goto_step_id); last may be (None, goto)
    branches: list[tuple[Optional[Callable[[dict], bool]], str]] = field(default_factory=list)
    # approval node
    approvers: list[str] = field(default_factory=list)
    # default next step (None => end of workflow)
    next: Optional[str] = None


@dataclass
class Workflow:
    name: str
    steps: dict[str, Step]           # step_id -> Step
    start: str                       # id of the first step
    budget_usd: float = 1.0
    id: str = field(default_factory=lambda: new_id("wf"))


# --- runtime ----------------------------------------------------------------

@dataclass
class Event:
    """One immutable entry in the append-only trace."""
    run_id: str
    type: str
    payload: dict[str, Any]
    step_id: Optional[str] = None
    cost_usd: float = 0.0
    id: int = field(default_factory=lambda: next(_counter))


@dataclass
class Run:
    workflow_id: str
    tenant_id: str
    trigger_event: dict[str, Any]
    status: RunStatus = RunStatus.PENDING
    context: dict[str, Any] = field(default_factory=dict)   # shared data bag
    cost_usd: float = 0.0
    events: list[Event] = field(default_factory=list)
    result: Optional[dict[str, Any]] = None
    id: str = field(default_factory=lambda: new_id("run"))

    def log(self, type: str, payload: dict, step_id: str | None = None,
            cost_usd: float = 0.0) -> Event:
        """Append to the trace and roll up cost."""
        ev = Event(run_id=self.id, type=type, payload=payload,
                   step_id=step_id, cost_usd=cost_usd)
        self.events.append(ev)
        self.cost_usd += cost_usd
        return ev
