"""Worked end-to-end example: invoice triage.

Run with:  python -m bass.example_run

This assembles a tenant, one agent, two tools, a policy set, and a 4-step workflow,
then fires a trigger and prints the full trace. The invoice is $8,200 — over the
$5,000 threshold — so the AP-entry tool requires approval, which our demo approver
grants.
"""

from __future__ import annotations

from .models import Agent, Run, Step, Tenant, Workflow
from .orchestrator import Orchestrator
from .policy import PolicyEngine, default_policies
from .tools import default_registry


def build_workflow() -> Workflow:
    steps = {
        "extract": Step(
            id="extract", node_type="agent", agent="invoice_extractor",
            next="validate",
        ),
        "validate": Step(
            id="validate", node_type="tool", tool="lookup_vendor",
            args={"vendor": lambda ctx: ctx["extract"]["vendor"]},
            next="decide",
        ),
        "decide": Step(
            id="decide", node_type="branch",
            branches=[
                # (predicate, goto) — first true wins; None = else
                (lambda ctx: ctx["extract"]["amount"] > 5000, "post"),
                (None, "post"),
            ],
        ),
        "post": Step(
            id="post", node_type="tool", tool="create_ap_entry",
            args={
                "vendor": lambda ctx: ctx["extract"]["vendor"],
                "invoice_number": lambda ctx: ctx["extract"]["invoice_number"],
                "amount": lambda ctx: ctx["extract"]["amount"],
            },
            next=None,
        ),
    }
    return Workflow(name="invoice-triage", steps=steps, start="extract", budget_usd=0.50)


def demo_approver(agent, tool, args, run: Run) -> bool:
    """Stands in for a human clicking 'approve' in the console."""
    run.log("approval_requested",
            {"tool": tool.name, "amount": args.get("amount"), "approvers": ["finance-team"]},
            step_id="post")
    granted = True   # a human would decide here
    run.log("approval_resolved",
            {"tool": tool.name, "granted": granted, "decided_by": "j.doe@finance"},
            step_id="post")
    return granted


def print_trace(run: Run) -> None:
    print(f"\nRun {run.id}  status={run.status.value}  cost=${run.cost_usd:.4f}")
    print("-" * 72)
    for ev in run.events:
        loc = f"[{ev.step_id}]" if ev.step_id else "[-]"
        print(f"  #{ev.id:<3} {ev.type:<20} {loc:<12} {ev.payload}")
    print("-" * 72)
    print(f"Result: {run.result}\n")


def main() -> None:
    tenant = Tenant(name="Acme Inc")
    agents = {
        "invoice_extractor": Agent(
            name="invoice_extractor",
            role="Extract structured data from vendor invoices.",
            instructions="Return vendor, invoice_number, amount, currency. Never guess.",
            model="claude-sonnet-5",
            tools=["lookup_vendor"],
        ),
    }
    tools = default_registry()
    policy = PolicyEngine(default_policies())

    orch = Orchestrator(tenant, agents, tools, policy, approval_fn=demo_approver)
    wf = build_workflow()

    trigger_event = {
        "source": "email",
        "vendor": "Globex Corp",
        "invoice_number": "INV-4471",
        "amount": 8200.0,
    }
    run = orch.fire(wf, trigger_event)
    print_trace(run)


if __name__ == "__main__":
    main()
