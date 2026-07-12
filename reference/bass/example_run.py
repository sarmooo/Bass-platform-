"""Worked end-to-end example: invoice triage (offline, durable).

Run with:  python -m bass.example_run

Assembles a tenant, one agent, two connector-backed tools, a policy set, and a
4-step workflow, persists everything to a SQLite store, fires a trigger, and
prints the durable trace read back from the store. The $8,200 invoice exceeds
the $5,000 threshold, so posting the AP entry requires approval.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .config import Config
from .models import Agent, Run, Step, Tenant, Workflow
from .observability import configure_logging
from .orchestrator import Orchestrator
from .policy import PolicyEngine, default_policies
from .store import SQLiteStore
from .tools import default_registry

INVOICE_SCHEMA = {
    "type": "object",
    "properties": {
        "vendor": {"type": "string"},
        "invoice_number": {"type": "string"},
        "amount": {"type": "number"},
        "currency": {"type": "string"},
    },
    "required": ["vendor", "invoice_number", "amount", "currency"],
}


def build_workflow() -> Workflow:
    steps = {
        "extract": Step(id="extract", node_type="agent", agent="invoice_extractor",
                        next="validate"),
        "validate": Step(id="validate", node_type="tool", tool="lookup_vendor",
                         args={"vendor": lambda ctx: ctx["extract"]["vendor"]},
                         next="decide"),
        "decide": Step(id="decide", node_type="branch", branches=[
            (lambda ctx: ctx["extract"]["amount"] > 5000, "post"),
            (None, "post"),
        ]),
        "post": Step(id="post", node_type="tool", tool="create_ap_entry", args={
            "vendor": lambda ctx: ctx["extract"]["vendor"],
            "invoice_number": lambda ctx: ctx["extract"]["invoice_number"],
            "amount": lambda ctx: ctx["extract"]["amount"],
        }, next=None),
    }
    return Workflow(id="wf_invoice_triage", name="invoice-triage", steps=steps,
                    start="extract", budget_usd=0.50, max_steps=25)


def demo_approver(run: Run, step, tool_name, args) -> bool:
    """Stands in for a human approving in the console. Returns the decision."""
    return True


def build_agents() -> dict[str, Agent]:
    return {
        "invoice_extractor": Agent(
            name="invoice_extractor",
            role="Extract structured data from vendor invoices.",
            instructions="Return vendor, invoice_number, amount, currency. Never guess.",
            model="claude-sonnet-5", tools=["lookup_vendor"], output_schema=INVOICE_SCHEMA),
    }


def print_trace(orch: Orchestrator, run: Run) -> None:
    print(f"\nRun {run.id}  status={run.status.value}  cost=${run.cost_usd:.4f}")
    print("-" * 74)
    for ev in orch.trace(run.id):
        loc = f"[{ev.step_id}]" if ev.step_id else "[-]"
        print(f"  #{ev.seq:<3} {ev.type:<22} {loc:<12} {ev.payload}")
    print("-" * 74)
    print(f"Result: {run.result}\n")


def main() -> None:
    cfg = Config()
    configure_logging(cfg.log_level, as_json=False)
    db = Path(tempfile.mkdtemp()) / "bass-example.db"
    store = SQLiteStore(str(db))

    orch = Orchestrator(store, Tenant(name="Acme Inc"), build_agents(),
                        default_registry(), PolicyEngine(default_policies()),
                        approval_fn=demo_approver, config=cfg)

    trigger = {"source": "email", "message_id": "<inv-4471@acme>",
               "vendor": "Globex Corp", "invoice_number": "INV-4471", "amount": 8200.0}
    run = orch.fire(build_workflow(), trigger)
    print_trace(orch, run)
    store.close()


if __name__ == "__main__":
    main()
