"""Tool registry, example connector-backed tools, and an offline model double.

Tools are the only way an agent touches an external system. Each tool names a
connector; `ToolRegistry.execute` runs the tool's operation *through* that
connector so every call inherits timeout, retry, and circuit-breaking.

The example "accounting" connector is an in-process fake with the production
behavior that matters: its AP-entry write is **idempotent on a key**, so a retry
or crash-replay never double-posts.
"""

from __future__ import annotations

import threading
from typing import Any

from .connectors import Connector
from .errors import ToolError
from .models import Agent, Tool


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._connectors: dict[str, Connector] = {}

    def register_connector(self, connector: Connector) -> None:
        self._connectors[connector.name] = connector

    def register(self, tool: Tool) -> None:
        if tool.connector not in self._connectors:
            raise ToolError(f"tool '{tool.name}' names unknown connector '{tool.connector}'")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise ToolError(f"unknown tool: {name}")
        return self._tools[name]

    def execute(self, name: str, args: dict) -> Any:
        tool = self.get(name)
        connector = self._connectors[tool.connector]
        return connector.run(tool.fn, args)


# --- example connector-backed operations ------------------------------------

_KNOWN_VENDORS = {
    "Globex Corp": {"id": "V-100", "currency": "USD"},
    "Initech": {"id": "V-200", "currency": "USD"},
}


class _AccountingSystem:
    """Stand-in external system. AP-entry creation is idempotent on a key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}      # idempotency_key -> entry

    def lookup_vendor(self, args: dict) -> Any:
        vendor = args.get("vendor")
        rec = _KNOWN_VENDORS.get(vendor)
        return {"vendor": vendor, "id": (rec or {}).get("id"), "known": rec is not None}

    def create_ap_entry(self, args: dict) -> Any:
        # The connector honors the idempotency key: a duplicate call returns the
        # original entry instead of creating a second one.
        key = args.get("_idempotency_key") or args.get("invoice_number")
        with self._lock:
            if key in self._entries:
                return self._entries[key]
            entry = {
                "vendor": args.get("vendor"),
                "invoice_number": args.get("invoice_number"),
                "amount": args.get("amount"),
                "status": "posted",
            }
            self._entries[key] = entry
            return entry

    def count(self) -> int:
        return len(self._entries)


def default_registry(accounting: _AccountingSystem | None = None) -> ToolRegistry:
    acct = accounting or _AccountingSystem()
    reg = ToolRegistry()
    reg.register_connector(Connector("accounting"))
    reg.register(Tool(
        name="lookup_vendor",
        description="Look up a vendor by name; returns id and known-status.",
        connector="accounting", fn=acct.lookup_vendor, side_effect=False,
        input_schema={"type": "object",
                      "properties": {"vendor": {"type": "string"}},
                      "required": ["vendor"]},
    ))
    reg.register(Tool(
        name="create_ap_entry",
        description="Create an accounts-payable entry in the accounting system.",
        connector="accounting", fn=acct.create_ap_entry, side_effect=True,
        input_schema={"type": "object",
                      "properties": {"vendor": {"type": "string"},
                                     "invoice_number": {"type": "string"},
                                     "amount": {"type": "number"}},
                      "required": ["vendor", "invoice_number", "amount"]},
    ))
    reg._accounting = acct                        # expose for the example/tests
    return reg


# --- offline model double ---------------------------------------------------

class MockModel:
    """Deterministic model double implementing the `respond` interface.

    Used for offline runs and the test suite. `providers.AnthropicModel` is the
    real implementation; the agent loop is identical for both.
    """

    def respond(self, agent: Agent, task: dict, scratch: dict) -> dict:
        if agent.name == "invoice_extractor":
            email = task.get("email", {})
            return {"type": "final", "cost_usd": 0.002, "output": {
                "vendor": email.get("vendor", "Globex Corp"),
                "invoice_number": email.get("invoice_number", "INV-4471"),
                "amount": email.get("amount", 8200.0),
                "currency": "USD",
            }}
        return {"type": "final", "cost_usd": 0.001, "output": task}
