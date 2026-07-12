"""Concurrency / contention tests.

Fires many runs of the same trigger concurrently and asserts the platform's
correctness guarantees hold under contention: a redelivered trigger creates the
run exactly once, and the mutating side effect posts exactly once. Runs against
whatever store `BASS_TEST_DB_URL` selects — real concurrency on PostgreSQL in CI.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from bass.example_run import build_agents, build_workflow, demo_approver
from bass.models import Tenant
from bass.orchestrator import Orchestrator
from bass.policy import PolicyEngine, default_policies
from bass.store import SQLiteStore, open_store
from bass.tools import _AccountingSystem, default_registry


def make_store():
    url = os.environ.get("BASS_TEST_DB_URL")
    return open_store(url) if url else SQLiteStore(str(Path(tempfile.mkdtemp()) / "c.db"))


class ConcurrencyTests(unittest.TestCase):
    def test_same_trigger_fired_concurrently_dedups_and_posts_once(self):
        store = make_store()
        acct = _AccountingSystem()
        tools = default_registry(acct)
        tenant = Tenant(name="Acme")
        trigger = {"source": "email", "message_id": "<concurrent@acme>",
                   "vendor": "Globex Corp", "invoice_number": "INV-4471",
                   "amount": 8200.0}
        wf = build_workflow()

        def fire_once(_):
            # Each thread gets its own orchestrator sharing the store + accounting.
            orch = Orchestrator(store, tenant, build_agents(), tools,
                                PolicyEngine(default_policies()), approval_fn=demo_approver)
            return orch.fire(wf, trigger).id

        with ThreadPoolExecutor(max_workers=16) as pool:
            run_ids = list(pool.map(fire_once, range(32)))

        self.assertEqual(len(set(run_ids)), 1, "all fires must resolve to one run")
        self.assertEqual(acct.count(), 1, "AP entry must post exactly once")
        store.close()

    def test_distinct_triggers_each_run_once(self):
        store = make_store()
        acct = _AccountingSystem()
        tools = default_registry(acct)
        tenant = Tenant(name="Beta")
        wf = build_workflow()

        def fire(i):
            orch = Orchestrator(store, tenant, build_agents(), tools,
                                PolicyEngine(default_policies()), approval_fn=demo_approver)
            trigger = {"source": "email", "message_id": f"<n-{i}@beta>",
                       "vendor": "Globex Corp", "invoice_number": f"INV-{i}",
                       "amount": 8200.0}
            return orch.fire(wf, trigger).id

        with ThreadPoolExecutor(max_workers=16) as pool:
            run_ids = list(pool.map(fire, range(20)))

        self.assertEqual(len(set(run_ids)), 20, "distinct triggers → distinct runs")
        self.assertEqual(acct.count(), 20, "each invoice posts once")
        store.close()


if __name__ == "__main__":
    unittest.main()
