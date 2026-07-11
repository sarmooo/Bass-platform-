"""Bass — reference implementation of an enterprise AI automation platform.

A compact but production-shaped implementation of the core described in ../docs:
durable, crash-safe, idempotent workflow execution; a fail-closed policy gate;
connector reliability; secret/PII redaction; enforced tenant isolation; budgets;
and structured observability.

External infrastructure is abstracted behind interfaces with working defaults
(SQLite `Store`, env-backed `SecretsProvider`) so a deployment can swap in
Postgres, a KMS vault, a durable-execution backend, and real connectors without
touching the engine.
"""

from .config import Config
from .models import Agent, Run, Step, Tenant, Tool, Workflow
from .orchestrator import Orchestrator
from .policy import PolicyEngine, default_policies
from .store import SQLiteStore, Store
from .tools import ToolRegistry, default_registry

__all__ = [
    "Config", "Agent", "Run", "Step", "Tenant", "Tool", "Workflow",
    "Orchestrator", "PolicyEngine", "default_policies",
    "SQLiteStore", "Store", "ToolRegistry", "default_registry",
]
