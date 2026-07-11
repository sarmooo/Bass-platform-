"""Live end-to-end example: invoice triage against the real Claude API.

Run with:  python -m bass.example_run_live

Identical to example_run.py except the `invoice_extractor` agent is driven by
providers.AnthropicModel (a real Claude call) instead of the offline MockModel.
Everything else — the deterministic workflow engine, the policy gate, the
approval step, the traced event log — is unchanged, which is the whole point:
only the model is swapped.

Requires `pip install anthropic` and credentials (ANTHROPIC_API_KEY or an
`ant auth login` profile). This makes a real, billed API call.
"""

from __future__ import annotations

import sys

from .example_run import build_workflow, demo_approver, print_trace
from .models import Agent, Tenant
from .orchestrator import Orchestrator
from .policy import PolicyEngine, default_policies
from .tools import default_registry

# The extractor now returns real structured JSON, so give it an output schema.
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


def main() -> None:
    try:
        from .providers import AnthropicModel
    except ImportError:
        sys.exit("The 'anthropic' package is required: pip install anthropic")

    tenant = Tenant(name="Acme Inc")
    agents = {
        "invoice_extractor": Agent(
            name="invoice_extractor",
            role="Extract structured data from vendor invoices.",
            instructions=(
                "You receive a raw invoice as JSON in <task_input>. Extract the "
                "vendor, invoice_number, amount, and currency. If a field is "
                "missing, use null — never guess."
            ),
            model="claude-sonnet-5",      # per-step model selection: extraction → Sonnet
            tools=["lookup_vendor"],
            output_schema=INVOICE_SCHEMA,
        ),
    }
    tools = default_registry()
    policy = PolicyEngine(default_policies())

    try:
        model = AnthropicModel(tools)      # constructs the Anthropic client
    except Exception as exc:               # missing creds, etc.
        sys.exit(f"Could not initialize the Anthropic client: {exc}")

    orch = Orchestrator(tenant, agents, tools, policy,
                        approval_fn=demo_approver, model=model)

    trigger_event = {
        "source": "email",
        "raw": "INVOICE\nGlobex Corp\nInvoice #: INV-4471\nTotal due: $8,200.00 USD",
    }
    run = orch.fire(build_workflow(), trigger_event)
    print_trace(run)


if __name__ == "__main__":
    main()
