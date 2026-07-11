"""HTTP API tests — auth, RBAC, async approvals, tenant isolation.

Skipped automatically if the [api] extra isn't installed. In CI the api extra is
installed, so these run in-process via FastAPI's TestClient (no server needed).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from fastapi.testclient import TestClient
    _HAVE_API = True
except ImportError:
    _HAVE_API = False

from bass.example_run import build_agents, build_workflow
from bass.policy import PolicyEngine, default_policies
from bass.store import SQLiteStore
from bass.tools import _AccountingSystem, default_registry

SECRET = "test-secret"


def _service():
    from bass.api import ApiService
    store = SQLiteStore(str(Path(tempfile.mkdtemp()) / "api.db"))
    acct = _AccountingSystem()
    svc = ApiService(store, build_agents(), default_registry(acct),
                     PolicyEngine(default_policies()), jwt_secret=SECRET)
    svc.register_workflow(build_workflow())
    return svc, acct


def _token(tenant="acme", subject="u1", roles=("builder", "approver")):
    from bass.auth import mint_token
    return mint_token(SECRET, tenant, subject, list(roles))


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@unittest.skipUnless(_HAVE_API, "[api] extra not installed")
class ApiTests(unittest.TestCase):
    def setUp(self):
        from bass.api import create_app
        self.svc, self.acct = _service()
        self.client = TestClient(create_app(self.svc))
        self.trigger = {"source": "email", "message_id": "<inv-4471@acme>",
                        "vendor": "Globex Corp", "invoice_number": "INV-4471",
                        "amount": 8200.0}

    def _fire(self, token=None):
        token = token or _token()
        return self.client.post("/v1/workflows/invoice-triage/runs",
                                json={"trigger": self.trigger}, headers=_auth(token))

    def test_healthz(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})

    def test_readyz(self):
        r = self.client.get("/readyz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ready")

    def test_metrics_requires_auth_and_reports(self):
        self.assertEqual(self.client.get("/metrics").status_code, 401)
        self._fire()                                    # generate some activity
        snap = self.client.get("/metrics", headers=_auth(_token())).json()
        self.assertIn("counters", snap)
        self.assertTrue(snap["counters"])               # non-empty after a run

    def test_missing_token_is_401(self):
        r = self.client.post("/v1/workflows/invoice-triage/runs", json={"trigger": {}})
        self.assertEqual(r.status_code, 401)

    def test_wrong_role_is_403(self):
        viewer = _token(roles=("viewer",))
        self.assertEqual(self._fire(viewer).status_code, 403)

    def test_async_approval_flow(self):
        # Fire → the $8,200 AP entry needs approval, so the run pauses.
        r = self._fire()
        self.assertEqual(r.status_code, 200)
        run_id = r.json()["run_id"]
        self.assertEqual(r.json()["status"], "waiting_approval")
        self.assertEqual(self.acct.count(), 0)              # nothing posted yet

        # The approval id is in the trace.
        trace = self.client.get(f"/v1/runs/{run_id}/trace", headers=_auth(_token())).json()
        requested = [e for e in trace["events"] if e["type"] == "approval_requested"]
        self.assertEqual(len(requested), 1)
        approval_id = requested[0]["payload"]["approval_id"]

        # Approve → the run resumes and completes; the AP entry posts exactly once.
        r2 = self.client.post(
            f"/v1/runs/{run_id}/approvals/{approval_id}",
            json={"approved": True}, headers=_auth(_token(roles=("approver",))))
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["status"], "succeeded")
        self.assertEqual(self.acct.count(), 1)

    def test_reject_approval_fails_run(self):
        run_id = self._fire().json()["run_id"]
        trace = self.client.get(f"/v1/runs/{run_id}/trace", headers=_auth(_token())).json()
        approval_id = next(e["payload"]["approval_id"] for e in trace["events"]
                           if e["type"] == "approval_requested")
        r = self.client.post(f"/v1/runs/{run_id}/approvals/{approval_id}",
                             json={"approved": False},
                             headers=_auth(_token(roles=("approver",))))
        self.assertEqual(r.json()["status"], "failed")
        self.assertEqual(self.acct.count(), 0)

    def test_list_runs(self):
        for i in range(3):
            self.client.post("/v1/workflows/invoice-triage/runs",
                             json={"trigger": {**self.trigger, "message_id": f"<m{i}>"}},
                             headers=_auth(_token()))
        listed = self.client.get("/v1/runs", headers=_auth(_token())).json()
        self.assertGreaterEqual(len(listed["runs"]), 3)
        waiting = self.client.get("/v1/runs?status=waiting_approval",
                                  headers=_auth(_token())).json()
        self.assertTrue(all(r["status"] == "waiting_approval" for r in waiting["runs"]))

    def test_cancel_waiting_run(self):
        run_id = self._fire().json()["run_id"]        # pauses at approval
        r = self.client.post(f"/v1/runs/{run_id}/cancel", headers=_auth(_token()))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "canceled")
        self.assertEqual(self.acct.count(), 0)         # nothing posted
        # a canceled run reads back canceled
        got = self.client.get(f"/v1/runs/{run_id}", headers=_auth(_token())).json()
        self.assertEqual(got["status"], "canceled")

    def test_tenant_isolation_across_tokens(self):
        run_id = self._fire().json()["run_id"]
        other = _token(tenant="other-co", subject="u9", roles=("viewer",))
        self.assertEqual(
            self.client.get(f"/v1/runs/{run_id}", headers=_auth(other)).status_code, 404)

    def test_approver_role_required_to_resolve(self):
        run_id = self._fire().json()["run_id"]
        trace = self.client.get(f"/v1/runs/{run_id}/trace", headers=_auth(_token())).json()
        approval_id = next(e["payload"]["approval_id"] for e in trace["events"]
                           if e["type"] == "approval_requested")
        # A builder-only token may fire but not approve.
        builder = _token(roles=("builder",))
        r = self.client.post(f"/v1/runs/{run_id}/approvals/{approval_id}",
                             json={"approved": True}, headers=_auth(builder))
        self.assertEqual(r.status_code, 403)


if __name__ == "__main__":
    unittest.main()
