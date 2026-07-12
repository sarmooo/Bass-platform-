"""Core domain types.

Plain, typed dataclasses. Persistence lives in `store.py`; these carry no I/O.
Mirrors the entities in ../docs/03-data-model.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# --- enums ------------------------------------------------------------------

class RunStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"


class PolicyEffect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


# --- tenancy ----------------------------------------------------------------

@dataclass
class Tenant:
    name: str
    id: str = field(default_factory=lambda: new_id("tenant"))
    settings: dict[str, Any] = field(default_factory=dict)


# --- tools & agents ---------------------------------------------------------

@dataclass
class Tool:
    """A capability exposed to an agent, backed by a connector.

    `side_effect=True` marks a mutating tool (stricter policy, requires an
    idempotency key on execution). `input_schema` is the typed contract the
    model sees and the runtime validates against.
    """
    name: str
    description: str
    connector: str                              # connector name that backs it
    fn: Callable[[dict], Any]
    side_effect: bool = False
    cost_usd: float = 0.0
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}})


@dataclass
class Agent:
    """An LLM worker: role + instructions + a least-privilege tool allow-list."""
    name: str
    role: str
    instructions: str
    model: str = "claude-sonnet-5"
    tools: list[str] = field(default_factory=list)   # allow-list of tool names
    max_steps: int = 8
    max_tokens: int = 4096
    output_schema: Optional[dict[str, Any]] = None


# --- workflows --------------------------------------------------------------

@dataclass
class Step:
    """One node in a workflow: agent | tool | branch | approval | terminate."""
    id: str
    node_type: str
    # agent node
    agent: Optional[str] = None
    # tool node
    tool: Optional[str] = None
    args: dict[str, Any] = field(default_factory=dict)
    # branch node: [(predicate, goto_step_id)]; a None predicate is the else arm
    branches: list[tuple[Optional[Callable[[dict], bool]], str]] = field(default_factory=list)
    # approval node
    approvers: list[str] = field(default_factory=list)
    # reliability
    max_attempts: int = 3
    compensate_with: Optional[str] = None       # step id to run to undo, on failure
    # default next step (None ends the workflow)
    next: Optional[str] = None


@dataclass
class Workflow:
    name: str
    steps: dict[str, Step]
    start: str
    version: int = 1
    budget_usd: float = 1.0
    max_steps: int = 50
    id: str = field(default_factory=lambda: new_id("wf"))


# --- runtime records (hydrated from the store) ------------------------------

@dataclass
class Event:
    run_id: str
    tenant_id: str
    type: str
    payload: dict[str, Any]
    step_id: Optional[str] = None
    actor: str = "system"
    cost_usd: float = 0.0
    seq: int = 0                                 # assigned by the store (monotonic)


@dataclass
class Run:
    tenant_id: str
    workflow_id: str
    workflow_version: int
    trigger_event: dict[str, Any]
    idempotency_key: str
    status: RunStatus = RunStatus.PENDING
    cursor_step: Optional[str] = None           # next step to execute (durable)
    context: dict[str, Any] = field(default_factory=dict)
    cost_usd: float = 0.0
    steps_used: int = 0
    result: Optional[dict[str, Any]] = None
    id: str = field(default_factory=lambda: new_id("run"))
