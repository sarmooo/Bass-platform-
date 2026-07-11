"""Tool registry, example tools, and a mock model.

Tools are the only way an agent touches the outside world. Here they are backed by
in-memory fakes so the demo is offline and deterministic. The MockModel stands in for
a real LLM call: given an agent + task it returns either a tool-call request or a
final structured answer.
"""

from __future__ import annotations

from typing import Any

from .models import Agent, Tool


# --- tool registry ----------------------------------------------------------

class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name]


# --- example connector-backed tools (fakes) ---------------------------------

_KNOWN_VENDORS = {
    "Globex Corp": {"id": "V-100", "currency": "USD"},
    "Initech": {"id": "V-200", "currency": "USD"},
}

_posted_ap_entries: list[dict] = []   # stands in for the accounting system


def _lookup_vendor(args: dict) -> Any:
    vendor = args.get("vendor")
    return _KNOWN_VENDORS.get(vendor, {"id": None, "known": False}) | {"vendor": vendor}


def _create_ap_entry(args: dict) -> Any:
    # Idempotency: a real connector would honor an idempotency key so retries are safe.
    entry = {
        "vendor": args.get("vendor"),
        "invoice_number": args.get("invoice_number"),
        "amount": args.get("amount"),
        "status": "posted",
    }
    _posted_ap_entries.append(entry)
    return entry


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(Tool(
        name="lookup_vendor",
        description="Look up a vendor by name; returns id and known-status.",
        fn=_lookup_vendor,
        side_effect=False,
        cost_usd=0.0,
    ))
    reg.register(Tool(
        name="create_ap_entry",
        description="Create an accounts-payable entry in the accounting system.",
        fn=_create_ap_entry,
        side_effect=True,          # mutating => stricter policy
        cost_usd=0.0,
    ))
    return reg


# --- mock model -------------------------------------------------------------

class MockModel:
    """Deterministic stand-in for an LLM.

    Real deployment: replace `respond` with an Anthropic API call that passes the
    agent's tool allow-list via the native tool-use interface, and parse the model's
    tool_use / text blocks. The agent loop in agent.py is written against this same
    shape, so nothing else changes.
    """

    # A response is either a tool call or a final answer.
    def respond(self, agent: Agent, task: dict, scratch: dict) -> dict:
        # The 'invoice_extractor' agent: pretend we OCR'd + parsed the email.
        if agent.name == "invoice_extractor":
            email = task.get("email", {})
            return {
                "type": "final",
                "cost_usd": 0.002,
                "output": {
                    "vendor": email.get("vendor", "Globex Corp"),
                    "invoice_number": email.get("invoice_number", "INV-4471"),
                    "amount": email.get("amount", 8200.0),
                    "currency": "USD",
                },
            }
        # Fallback: echo the task.
        return {"type": "final", "cost_usd": 0.001, "output": task}
