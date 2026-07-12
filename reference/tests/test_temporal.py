"""Temporal integration test — a Bass run driven by a real Temporal workflow.

Uses Temporal's self-contained local test server (downloaded by the SDK), so no
external service is required. Gated on BASS_TEST_TEMPORAL=1 (set in the dedicated
CI job) and the presence of `temporalio`.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

_ENABLED = os.environ.get("BASS_TEST_TEMPORAL") == "1"
try:
    import temporalio  # noqa: F401
    _HAVE = True
except ImportError:
    _HAVE = False


@unittest.skipUnless(_ENABLED and _HAVE, "set BASS_TEST_TEMPORAL=1 with temporalio installed")
class TemporalTests(unittest.TestCase):
    def test_run_executes_via_temporal_workflow(self):
        asyncio.run(self._scenario())

    async def _scenario(self):
        from temporalio.testing import WorkflowEnvironment
        from temporalio.worker import Worker

        from bass.models import Run
        from bass.orchestrator import derive_idempotency_key
        from bass.runner import build_from_env
        from bass.temporal_adapter import BassRunWorkflow, resume_run_activity

        os.environ["BASS_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "temporal.db")
        runner, wf = build_from_env()
        tenant = "acme"
        runner.store.ensure_tenant(tenant, "Acme", {})
        # amount <= $5,000 → no approval needed, so it completes in one activity.
        trigger = {"source": "email", "message_id": "<temporal@acme>",
                   "vendor": "Globex Corp", "invoice_number": "INV-9", "amount": 100.0}
        run = Run(tenant_id=tenant, workflow_id=wf.id, workflow_version=wf.version,
                  trigger_event=trigger,
                  idempotency_key=derive_idempotency_key(trigger), cursor_step=wf.start)
        run, _ = runner.store.create_or_get_run(run)

        async with await WorkflowEnvironment.start_local() as env:
            async with Worker(env.client, task_queue="bass-tq",
                              workflows=[BassRunWorkflow],
                              activities=[resume_run_activity]):
                status = await env.client.execute_workflow(
                    BassRunWorkflow.run, {"tenant_id": tenant, "run_id": run.id},
                    id=f"wf-{run.id}", task_queue="bass-tq")

        self.assertEqual(status, "succeeded")
        final = runner.store.load_run(tenant, run.id)
        self.assertEqual(final.status.value, "succeeded")


if __name__ == "__main__":
    unittest.main()
