"""Rate-limiter tests — the in-memory limiter, the API 429 path, and (in CI) a
real Redis-backed limiter.

The Redis case runs only when BASS_TEST_REDIS_URL is set (CI's integration job);
elsewhere it skips. The in-memory and API cases have no external dependency.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from bass.ratelimit import InMemoryRateLimiter

try:
    from fastapi.testclient import TestClient
    _HAVE_API = True
except ImportError:
    _HAVE_API = False


class _Clock:
    """A hand-advanced clock so window rollover is deterministic (no sleeping)."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class InMemoryRateLimiterTests(unittest.TestCase):
    def test_allows_up_to_limit_then_denies(self):
        rl = InMemoryRateLimiter(limit=2, window_s=60.0, clock=_Clock())
        self.assertTrue(rl.allow("acme"))     # 1
        self.assertTrue(rl.allow("acme"))     # 2
        self.assertFalse(rl.allow("acme"))    # 3 → over the limit

    def test_window_rollover_resets_the_count(self):
        clock = _Clock()
        rl = InMemoryRateLimiter(limit=1, window_s=60.0, clock=clock)
        self.assertTrue(rl.allow("acme"))
        self.assertFalse(rl.allow("acme"))
        clock.t += 60.0                       # next window
        self.assertTrue(rl.allow("acme"))     # count reset

    def test_keys_are_isolated(self):
        rl = InMemoryRateLimiter(limit=1, window_s=60.0, clock=_Clock())
        self.assertTrue(rl.allow("acme"))
        self.assertTrue(rl.allow("globex"))   # a different tenant has its own bucket
        self.assertFalse(rl.allow("acme"))


def _service(rate_limiter=None):
    from bass.api import ApiService
    from bass.example_run import build_agents, build_workflow
    from bass.policy import PolicyEngine, default_policies
    from bass.store import SQLiteStore
    from bass.tools import _AccountingSystem, default_registry

    store = SQLiteStore(str(Path(tempfile.mkdtemp()) / "rl.db"))
    svc = ApiService(store, build_agents(), default_registry(_AccountingSystem()),
                     PolicyEngine(default_policies()), jwt_secret="rl-secret",
                     rate_limiter=rate_limiter)
    svc.register_workflow(build_workflow())
    return svc


@unittest.skipUnless(_HAVE_API, "[api] extra not installed")
class ApiRateLimitTests(unittest.TestCase):
    def test_over_limit_fire_returns_429(self):
        from bass.api import create_app
        from bass.auth import mint_token

        svc = _service(rate_limiter=InMemoryRateLimiter(limit=1, window_s=60.0))
        client = TestClient(create_app(svc))
        token = mint_token("rl-secret", "acme", "u1", ["builder"])
        headers = {"Authorization": f"Bearer {token}"}

        def fire(mid: str):
            return client.post("/v1/workflows/invoice-triage/runs",
                               json={"trigger": {"message_id": mid, "amount": 10.0}},
                               headers=headers)

        self.assertEqual(fire("<a>").status_code, 200)   # first request: allowed
        self.assertEqual(fire("<b>").status_code, 429)   # second: rate limited

    def test_limit_is_per_tenant(self):
        from bass.api import create_app
        from bass.auth import mint_token

        svc = _service(rate_limiter=InMemoryRateLimiter(limit=1, window_s=60.0))
        client = TestClient(create_app(svc))

        def fire(tenant: str, mid: str):
            token = mint_token("rl-secret", tenant, "u1", ["builder"])
            return client.post("/v1/workflows/invoice-triage/runs",
                               json={"trigger": {"message_id": mid, "amount": 10.0}},
                               headers={"Authorization": f"Bearer {token}"})

        self.assertEqual(fire("acme", "<a>").status_code, 200)
        self.assertEqual(fire("globex", "<b>").status_code, 200)  # other tenant unaffected
        self.assertEqual(fire("acme", "<c>").status_code, 429)


@unittest.skipUnless(os.environ.get("BASS_TEST_REDIS_URL"), "BASS_TEST_REDIS_URL not set")
class RedisRateLimiterTests(unittest.TestCase):
    """Exercises RedisRateLimiter against a real Redis (CI integration job)."""

    def _client(self):
        import redis
        return redis.Redis.from_url(os.environ["BASS_TEST_REDIS_URL"])

    def test_allows_up_to_limit_then_denies(self):
        from bass.ratelimit import RedisRateLimiter

        clock = _Clock()
        client = self._client()
        # A unique key per run so repeated CI runs don't collide in a shared window.
        key = f"test-{int(clock.t)}-{id(self)}"
        rl = RedisRateLimiter(client, limit=2, window_s=60, clock=clock)
        self.assertTrue(rl.allow(key))
        self.assertTrue(rl.allow(key))
        self.assertFalse(rl.allow(key))

    def test_window_rollover_resets_the_count(self):
        from bass.ratelimit import RedisRateLimiter

        clock = _Clock()
        rl = RedisRateLimiter(self._client(), limit=1, window_s=60, clock=clock)
        key = f"roll-{int(clock.t)}-{id(self)}"
        self.assertTrue(rl.allow(key))
        self.assertFalse(rl.allow(key))
        clock.t += 60.0
        self.assertTrue(rl.allow(key))


if __name__ == "__main__":
    unittest.main()
