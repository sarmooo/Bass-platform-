"""OpenTelemetry tracing test.

Verifies the engine emits run and step spans by capturing them with an in-memory
exporter — no collector required. Skipped unless `opentelemetry` is installed
(the [otel] extra, present in the unit CI job).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter)
    _HAVE = True
except ImportError:
    _HAVE = False


@unittest.skipUnless(_HAVE, "opentelemetry not installed")
class OtelTests(unittest.TestCase):
    def test_run_and_step_spans_are_emitted(self):
        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        trace.set_tracer_provider(provider)          # process-global; set once

        from bass.example_run import build_agents, build_workflow, demo_approver
        from bass.models import Tenant
        from bass.orchestrator import Orchestrator
        from bass.policy import PolicyEngine, default_policies
        from bass.store import SQLiteStore
        from bass.tools import default_registry

        store = SQLiteStore(str(Path(tempfile.mkdtemp()) / "o.db"))
        orch = Orchestrator(store, Tenant(name="Acme"), build_agents(),
                            default_registry(), PolicyEngine(default_policies()),
                            approval_fn=demo_approver)
        trigger = {"source": "email", "message_id": "<otel@acme>",
                   "vendor": "Globex Corp", "invoice_number": "INV-1", "amount": 100.0}
        orch.fire(build_workflow(), trigger)

        spans = exporter.get_finished_spans()
        names = [s.name for s in spans]
        self.assertIn("bass.run", names)
        self.assertIn("bass.step", names)

        run_span = next(s for s in spans if s.name == "bass.run")
        self.assertEqual(run_span.attributes.get("bass.status"), "succeeded")
        self.assertEqual(run_span.attributes.get("bass.workflow"), "invoice-triage")

        step_spans = [s for s in spans if s.name == "bass.step"]
        node_types = {s.attributes.get("bass.node_type") for s in step_spans}
        self.assertIn("agent", node_types)
        self.assertIn("tool", node_types)


if __name__ == "__main__":
    unittest.main()
