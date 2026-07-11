"""Test suite for the Bass reference implementation.

Uses stdlib unittest so it runs with zero third-party dependencies (and under
pytest in CI). Each test asserts one production property of the platform.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from bass.agent import AgentRuntime
from bass.budgets import Budget
from bass.connectors import CircuitBreaker, Connector, ReliabilityConfig, Transient
from bass.errors import (BudgetExceeded, CircuitOpen, ConnectorError, PolicyDenied,
                         TenantIsolationError)
from bass.example_run import build_agents, build_workflow, demo_approver
from bass.models import Agent, PolicyEffect, Run, RunStatus, Tool, Workflow
from bass.policy import Policy, PolicyEngine, default_policies
from bass.redaction import REDACTED, redact
from bass.store import SQLiteStore, open_store
from bass.tools import _AccountingSystem, default_registry
from bass.workflow import WorkflowEngine


def _tmp_db() -> str:
    return str(Path(tempfile.mkdtemp()) / "t.db")


def make_store():
    """Build the store under test. Defaults to a fresh SQLite file; when
    BASS_TEST_DB_URL is set (a PostgreSQL DSN in CI), the identical suite runs
    against a real PostgreSQL server."""
    url = os.environ.get("BASS_TEST_DB_URL")
    return open_store(url) if url else SQLiteStore(_tmp_db())


def _run_seed(wf: Workflow, tenant_id: str, key: str = "k1") -> Run:
    return Run(tenant_id=tenant_id, workflow_id=wf.id, workflow_version=wf.version,
               trigger_event={"amount": 8200.0, "vendor": "Globex Corp",
                              "invoice_number": "INV-4471"},
               idempotency_key=key, cursor_step=wf.start)


def _make_engine(store, tools, approver=lambda *a, **k: True):
    policy = PolicyEngine(default_policies())
    runtime = AgentRuntime(__import__("bass.tools", fromlist=["MockModel"]).MockModel(),
                           tools, policy)
    engine = WorkflowEngine(store, build_agents(), tools, policy, runtime,
                            approval_fn=approver)
    runtime._emit = engine.emit
    return engine


# --------------------------------------------------------------------------- #

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.engine = PolicyEngine(default_policies())

    def _agent(self, tools):
        return Agent(name="a", role="", instructions="", tools=tools)

    def _tool(self, name, side_effect):
        return Tool(name=name, description="", connector="c", fn=lambda a: None,
                    side_effect=side_effect)

    def test_tool_outside_allow_list_denied(self):
        d = self.engine.check(self._agent([]), self._tool("x", False), {}, {})
        self.assertEqual(d.effect, PolicyEffect.DENY)

    def test_read_only_allowed(self):
        d = self.engine.check(self._agent(["x"]), self._tool("x", False), {}, {})
        self.assertEqual(d.effect, PolicyEffect.ALLOW)

    def test_large_ap_requires_approval(self):
        d = self.engine.check(self._agent(["create_ap_entry"]),
                              self._tool("create_ap_entry", True), {"amount": 9000}, {})
        self.assertEqual(d.effect, PolicyEffect.REQUIRE_APPROVAL)
        self.assertIn("finance-team", d.approvers)

    def test_small_ap_auto_allowed(self):
        d = self.engine.check(self._agent(["create_ap_entry"]),
                              self._tool("create_ap_entry", True), {"amount": 100}, {})
        self.assertEqual(d.effect, PolicyEffect.ALLOW)

    def test_mutating_tool_denied_by_default(self):
        d = self.engine.check(self._agent(["writer"]), self._tool("writer", True), {}, {})
        self.assertEqual(d.effect, PolicyEffect.DENY)

    def test_erroring_policy_fails_closed(self):
        def boom(a, t, args, ctx):
            raise ValueError("bad rule")
        eng = PolicyEngine([Policy(name="boom", version=1, priority=1, applies=boom,
                                   effect=PolicyEffect.ALLOW, reason="")])
        d = eng.check(self._agent(["x"]), self._tool("x", False), {}, {})
        self.assertEqual(d.effect, PolicyEffect.DENY)


class RedactionTests(unittest.TestCase):
    def test_secret_keys_and_pii_masked(self):
        out = redact({
            "password": "hunter2",
            "email": "alice@example.com",
            "nested": {"api_key": "abc", "note": "token is sk-ant-abcd1234efgh"},
            "amount": 8200.0,
        })
        self.assertEqual(out["password"], REDACTED)
        self.assertEqual(out["nested"]["api_key"], REDACTED)
        self.assertNotIn("alice@example.com", out["email"])
        self.assertIn(REDACTED, out["nested"]["note"])
        self.assertEqual(out["amount"], 8200.0)   # non-secret scalar untouched


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.store.ensure_tenant("tenant_a", "A", {})
        self.store.ensure_tenant("tenant_b", "B", {})
        self.wf = build_workflow()

    def tearDown(self):
        self.store.close()

    def test_trigger_dedup(self):
        r1, c1 = self.store.create_or_get_run(_run_seed(self.wf, "tenant_a", "same"))
        r2, c2 = self.store.create_or_get_run(_run_seed(self.wf, "tenant_a", "same"))
        self.assertTrue(c1)
        self.assertFalse(c2)                       # duplicate not created
        self.assertEqual(r1.id, r2.id)

    def test_tenant_isolation_on_load(self):
        r, _ = self.store.create_or_get_run(_run_seed(self.wf, "tenant_a", "iso"))
        with self.assertRaises(TenantIsolationError):
            self.store.load_run("tenant_b", r.id)
        with self.assertRaises(TenantIsolationError):
            self.store.list_events("tenant_b", r.id)

    def test_effect_ledger_roundtrip(self):
        self.assertIsNone(self.store.get_effect("tenant_a", "k"))
        self.store.record_effect("tenant_a", "k", "run", "step", "tool", {"ok": True})
        self.assertEqual(self.store.get_effect("tenant_a", "k"), {"ok": True})
        with self.assertRaises(TenantIsolationError):
            self.store.get_effect("tenant_b", "k")

    def test_cancel_run(self):
        r, _ = self.store.create_or_get_run(_run_seed(self.wf, "tenant_a", "cancel"))
        self.assertTrue(self.store.cancel_run("tenant_a", r.id))
        self.assertEqual(self.store.load_run("tenant_a", r.id).status, RunStatus.CANCELED)
        # already terminal → cannot cancel again
        self.assertFalse(self.store.cancel_run("tenant_a", r.id))
        # cross-tenant cancel is refused
        with self.assertRaises(TenantIsolationError):
            self.store.cancel_run("tenant_b", r.id)


class ConnectorTests(unittest.TestCase):
    def _cfg(self, **kw):
        base = dict(timeout_s=0.5, max_retries=3, backoff_base_s=0,
                    breaker_threshold=3, breaker_reset_s=10)
        base.update(kw)
        return ReliabilityConfig(**base)

    def test_retries_then_succeeds(self):
        calls = {"n": 0}
        def flaky(args):
            calls["n"] += 1
            if calls["n"] < 3:
                raise Transient("boom")
            return "ok"
        c = Connector("c", self._cfg(), sleep=lambda _: None)
        self.assertEqual(c.run(flaky, {}), "ok")
        self.assertEqual(calls["n"], 3)

    def test_gives_up_after_retries(self):
        c = Connector("c", self._cfg(max_retries=1), sleep=lambda _: None)
        with self.assertRaises(ConnectorError):
            c.run(lambda a: (_ for _ in ()).throw(Transient("always")), {})

    def test_timeout_is_transient(self):
        import time as _t
        c = Connector("c", self._cfg(timeout_s=0.05, max_retries=0), sleep=lambda _: None)
        with self.assertRaises(ConnectorError):
            c.run(lambda a: _t.sleep(0.4), {})

    def test_circuit_breaker_opens(self):
        clock = {"t": 0.0}
        b = CircuitBreaker(threshold=2, reset_s=5, now=lambda: clock["t"])
        b.record_failure(); self.assertTrue(b.allow())
        b.record_failure(); self.assertFalse(b.allow())    # open after 2
        clock["t"] = 6.0
        self.assertTrue(b.allow())                          # half-open after cooldown

    def test_open_breaker_blocks_call(self):
        c = Connector("c", self._cfg(breaker_threshold=1, max_retries=0), sleep=lambda _: None)
        with self.assertRaises(ConnectorError):
            c.run(lambda a: (_ for _ in ()).throw(Transient("x")), {})
        with self.assertRaises(CircuitOpen):
            c.run(lambda a: "would-succeed", {})


class BudgetTests(unittest.TestCase):
    def test_within_and_over(self):
        run = Run(tenant_id="t", workflow_id="w", workflow_version=1, trigger_event={},
                  idempotency_key="k")
        b = Budget(max_usd=0.01, max_steps=5)
        run.cost_usd = 0.005; run.steps_used = 2
        self.assertIsNone(b.check(run))
        run.cost_usd = 0.02
        self.assertIsNotNone(b.check(run))


class EngineDurabilityTests(unittest.TestCase):
    def setUp(self):
        self.store = make_store()
        self.store.ensure_tenant("acme", "Acme", {})
        self.acct = _AccountingSystem()
        self.tools = default_registry(self.acct)
        self.wf = build_workflow()

    def tearDown(self):
        self.store.close()

    def test_happy_path_posts_once(self):
        engine = _make_engine(self.store, self.tools)
        run, _ = self.store.create_or_get_run(_run_seed(self.wf, "acme", "h1"))
        final = engine.resume(run, self.wf)
        self.assertEqual(final.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.acct.count(), 1)

    def test_crash_before_final_checkpoint_then_resume_posts_exactly_once(self):
        engine = _make_engine(self.store, self.tools)
        run, _ = self.store.create_or_get_run(_run_seed(self.wf, "acme", "c1"))

        # Simulate a process crash: the final checkpoint (cursor advanced to end)
        # fails after the AP entry was already posted and its effect recorded.
        original = self.store.checkpoint
        state = {"crashed": False}
        def crashing(r, events):
            if r.cursor_step is None and not state["crashed"]:
                state["crashed"] = True
                raise RuntimeError("simulated crash before final checkpoint")
            return original(r, events)
        self.store.checkpoint = crashing

        with self.assertRaises(RuntimeError):
            engine.resume(run, self.wf)
        self.assertEqual(self.acct.count(), 1)             # side effect happened once

        # Restart: fresh engine, re-hydrate the run from the store, resume.
        self.store.checkpoint = original
        reloaded = self.store.load_run("acme", run.id)
        self.assertEqual(reloaded.cursor_step, "post")     # durable cursor before crash
        self.assertEqual(reloaded.status, RunStatus.RUNNING)

        engine2 = _make_engine(self.store, self.tools)
        final = engine2.resume(reloaded, self.wf)
        self.assertEqual(final.status, RunStatus.SUCCEEDED)
        self.assertEqual(self.acct.count(), 1)             # EXACTLY once across the crash

        types = [e.type for e in self.store.list_events("acme", run.id)]
        self.assertIn("tool_result_replayed", types)       # replayed, not re-executed

    def test_approval_rejected_fails_closed(self):
        engine = _make_engine(self.store, self.tools, approver=lambda *a, **k: False)
        run, _ = self.store.create_or_get_run(_run_seed(self.wf, "acme", "r1"))
        with self.assertRaises(PolicyDenied):
            engine.resume(run, self.wf)
        reloaded = self.store.load_run("acme", run.id)
        self.assertEqual(reloaded.status, RunStatus.FAILED)
        self.assertEqual(self.acct.count(), 0)             # nothing posted

    def test_budget_exceeded_fails_run(self):
        tight = build_workflow()
        tight.budget_usd = 0.001                            # extract costs 0.002
        engine = _make_engine(self.store, self.tools)
        run, _ = self.store.create_or_get_run(_run_seed(tight, "acme", "b1"))
        with self.assertRaises(BudgetExceeded):
            engine.resume(run, tight)
        reloaded = self.store.load_run("acme", run.id)
        self.assertEqual(reloaded.status, RunStatus.FAILED)


class EndToEndTests(unittest.TestCase):
    def test_orchestrator_dedup_across_two_fires(self):
        from bass.orchestrator import Orchestrator
        from bass.models import Tenant
        store = make_store()
        acct = _AccountingSystem()
        tools = default_registry(acct)
        orch = Orchestrator(store, Tenant(name="Acme"), build_agents(), tools,
                            PolicyEngine(default_policies()), approval_fn=demo_approver)
        trigger = {"source": "email", "message_id": "<dup@acme>",
                   "vendor": "Globex Corp", "invoice_number": "INV-4471", "amount": 8200.0}
        wf = build_workflow()
        r1 = orch.fire(wf, trigger)
        r2 = orch.fire(wf, trigger)                          # redelivered
        self.assertEqual(r1.id, r2.id)
        self.assertEqual(acct.count(), 1)                    # posted exactly once
        store.close()


if __name__ == "__main__":
    unittest.main()
