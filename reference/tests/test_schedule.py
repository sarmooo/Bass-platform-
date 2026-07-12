"""Scheduled trigger tests — the cron matcher and once-per-minute firing."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from bass.example_run import build_agents, build_workflow, demo_approver
from bass.models import Tenant
from bass.orchestrator import Orchestrator
from bass.policy import PolicyEngine, default_policies
from bass.schedule import Schedule, Scheduler, cron_matches
from bass.store import SQLiteStore
from bass.tools import _AccountingSystem, default_registry


class CronMatchTests(unittest.TestCase):
    def test_wildcard_matches_anything(self):
        self.assertTrue(cron_matches("* * * * *", datetime(2026, 7, 11, 13, 37)))

    def test_specific_minute_hour(self):
        self.assertTrue(cron_matches("0 9 * * *", datetime(2026, 7, 11, 9, 0)))
        self.assertFalse(cron_matches("0 9 * * *", datetime(2026, 7, 11, 9, 1)))
        self.assertFalse(cron_matches("0 9 * * *", datetime(2026, 7, 11, 10, 0)))

    def test_step_and_list_and_range(self):
        expr = "*/15 * * * *"
        self.assertTrue(cron_matches(expr, datetime(2026, 7, 11, 8, 30)))
        self.assertFalse(cron_matches(expr, datetime(2026, 7, 11, 8, 31)))
        self.assertTrue(cron_matches("0 9,17 * * *", datetime(2026, 7, 11, 17, 0)))
        self.assertTrue(cron_matches("0 9-11 * * *", datetime(2026, 7, 11, 10, 0)))

    def test_day_of_week(self):
        # 2026-07-13 is a Monday (cron dow 1).
        self.assertTrue(cron_matches("0 9 * * 1", datetime(2026, 7, 13, 9, 0)))
        self.assertFalse(cron_matches("0 9 * * 1", datetime(2026, 7, 14, 9, 0)))

    def test_dom_dow_or_semantics(self):
        # Both restricted → match if EITHER matches. 2026-07-01 is a Wednesday.
        expr = "0 0 1 * 5"  # 1st of month OR Friday
        self.assertTrue(cron_matches(expr, datetime(2026, 7, 1, 0, 0)))   # is the 1st
        self.assertTrue(cron_matches(expr, datetime(2026, 7, 3, 0, 0)))   # is a Friday
        self.assertFalse(cron_matches(expr, datetime(2026, 7, 2, 0, 0)))  # neither

    def test_invalid_expression(self):
        with self.assertRaises(ValueError):
            cron_matches("* * *", datetime(2026, 7, 11, 0, 0))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.store = SQLiteStore(str(Path(tempfile.mkdtemp()) / "s.db"))
        self.acct = _AccountingSystem()
        self.tools = default_registry(self.acct)
        self.tenant = Tenant(name="Acme")
        self.wf = build_workflow()

    def _fire(self, schedule: Schedule, trigger: dict):
        trigger = {**trigger, "vendor": "Globex Corp",
                   "invoice_number": "INV-SCHED", "amount": 100.0}
        orch = Orchestrator(self.store, self.tenant, build_agents(), self.tools,
                            PolicyEngine(default_policies()), approval_fn=demo_approver)
        return orch.fire(self.wf, trigger)

    def test_due_schedule_fires_once_per_minute(self):
        sched = Scheduler([Schedule(self.tenant.id, "invoice-triage", "*/5 * * * *")])
        now = datetime(2026, 7, 11, 12, 5)          # minute 5 matches */5

        first = sched.tick(now, self._fire)
        second = sched.tick(now, self._fire)         # same minute → dedup
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0].id, second[0].id)  # identical run
        self.assertEqual(self.acct.count(), 1)       # posted exactly once

    def test_not_due_does_not_fire(self):
        sched = Scheduler([Schedule(self.tenant.id, "invoice-triage", "0 9 * * *")])
        fired = sched.tick(datetime(2026, 7, 11, 12, 5), self._fire)
        self.assertEqual(fired, [])
        self.assertEqual(self.acct.count(), 0)

    def test_disabled_schedule_skipped(self):
        sched = Scheduler([Schedule(self.tenant.id, "invoice-triage", "* * * * *",
                                    enabled=False)])
        self.assertEqual(sched.tick(datetime(2026, 7, 11, 12, 5), self._fire), [])


if __name__ == "__main__":
    unittest.main()
