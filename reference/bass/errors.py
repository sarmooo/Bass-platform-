"""Typed error hierarchy.

Callers branch on these classes; nothing in the system raises bare Exception for
a condition another layer might reasonably handle.
"""

from __future__ import annotations


class BassError(Exception):
    """Base for every error raised by the platform."""


class ConfigError(BassError):
    """Invalid or missing configuration."""


class PolicyDenied(BassError):
    """A tool call was denied by the policy engine (fail-closed)."""


class BudgetExceeded(BassError):
    """A run exceeded its cost or step budget."""


class RateLimited(BassError):
    """A tenant exceeded its request rate limit."""


class ApprovalRequired(BassError):
    """A run is paused awaiting a human decision (not a failure)."""


class ConnectorError(BassError):
    """A connector failed after exhausting retries, or its breaker is open."""


class CircuitOpen(ConnectorError):
    """The connector's circuit breaker is open; the call was not attempted."""


class ToolError(BassError):
    """A tool could not be executed (unknown, bad args, or connector failure)."""


class TenantIsolationError(BassError):
    """An attempt to read or write data across a tenant boundary."""


class MaxStepsExceeded(BassError):
    """An agent step exceeded its configured step budget."""


class WorkflowError(BassError):
    """The workflow definition is invalid or a node type is unknown."""
