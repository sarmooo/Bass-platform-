"""Live end-to-end example: invoice triage against the real Claude API.

Run with:  python -m bass.example_run_live

Identical to example_run.py except the `invoice_extractor` agent is driven by
providers.AnthropicModel (a real Claude call) instead of the offline MockModel.
The durable store, policy gate, approval, and traced event log are unchanged.

Requires `pip install anthropic` and credentials (ANTHROPIC_API_KEY or an
`ant auth login` profile). This makes a real, billed API call.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from .config import Config
from .example_run import build_agents, build_workflow, demo_approver, print_trace
from .models import Tenant
from .observability import configure_logging
from .orchestrator import Orchestrator
from .policy import PolicyEngine, default_policies
from .store import SQLiteStore
from .tools import default_registry


def main() -> None:
    try:
        from .providers import AnthropicModel
    except ImportError:
        sys.exit("The 'anthropic' package is required: pip install anthropic")

    cfg = Config()
    configure_logging(cfg.log_level, as_json=False)
    tools = default_registry()

    try:
        model = AnthropicModel(tools)
    except Exception as exc:
        sys.exit(f"Could not initialize the Anthropic client: {exc}")

    db = Path(tempfile.mkdtemp()) / "bass-live.db"
    store = SQLiteStore(str(db))
    orch = Orchestrator(store, Tenant(name="Acme Inc"), build_agents(), tools,
                        PolicyEngine(default_policies()), approval_fn=demo_approver,
                        model=model, config=cfg)

    trigger = {"source": "email", "message_id": "<inv-4471-live@acme>",
               "raw": "INVOICE\nGlobex Corp\nInvoice #: INV-4471\nTotal due: $8,200.00 USD"}
    run = orch.fire(build_workflow(), trigger)
    print_trace(orch, run)
    store.close()


if __name__ == "__main__":
    main()
