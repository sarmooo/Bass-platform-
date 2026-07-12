"""Temporal durable-execution adapter.

Runs a Bass run under a Temporal workflow: Temporal provides durable
orchestration, retries, and visibility, while the Bass engine executes the steps
and the store remains the per-step durable log. This is the production path for
horizontally-scaled, crash-tolerant execution — an alternative driver to calling
`engine.resume` in-process.

The workflow invokes one activity that advances the run to completion (or pause);
Temporal's retry policy covers transient activity failures. Requires the
`[temporal]` extra (`temporalio`). The Bass engine is imported inside the activity
(activities are not sandboxed) so this module stays importable by the workflow
sandbox.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy


@activity.defn
async def resume_run_activity(inp: dict) -> str:
    """Advance a run to completion/pause; returns the final run status."""
    from .runner import build_from_env
    runner, _wf = build_from_env()
    # The engine is synchronous; run it off the event loop.
    run = await asyncio.to_thread(runner.resume, inp["tenant_id"], inp["run_id"])
    return run.status.value


@workflow.defn
class BassRunWorkflow:
    @workflow.run
    async def run(self, inp: dict) -> str:
        return await workflow.execute_activity(
            resume_run_activity, inp,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
