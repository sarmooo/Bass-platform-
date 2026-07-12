"""Microbenchmarks for the hot pure paths (pytest-benchmark).

Skipped in the normal suite (`--benchmark-skip` in pyproject); the perf CI job
runs them with `--benchmark-only` and records the timings. These are the
per-request/per-step operations whose latency the platform multiplies, so a
regression here matters.
"""

from __future__ import annotations

from datetime import datetime

from bass.auth import decode_token, mint_token
from bass.models import Agent, Tool
from bass.policy import PolicyEngine, default_policies
from bass.ratelimit import InMemoryRateLimiter
from bass.redaction import redact
from bass.schedule import cron_matches

_PAYLOAD = {
    "vendor": "Globex Corp", "amount": 8200.0,
    "email": "ap@globex.example.com", "api_key": "sk-ant-secret-abcd1234",
    "nested": {"note": "call bob@acme.example.com", "password": "hunter2"},
    "lines": [{"sku": "A-1", "qty": 3}, {"sku": "B-2", "qty": 1}],
}


def test_bench_redact(benchmark):
    benchmark(redact, _PAYLOAD)


def test_bench_cron_matches(benchmark):
    dt = datetime(2026, 7, 11, 9, 30)
    benchmark(cron_matches, "*/5 9-17 * * 1-5", dt)


def test_bench_rate_limiter(benchmark):
    rl = InMemoryRateLimiter(limit=1000, window_s=60.0)
    benchmark(rl.allow, "tenant-acme")


def test_bench_policy_check(benchmark):
    engine = PolicyEngine(default_policies())
    agent = Agent(name="a", role="", instructions="", tools=["create_ap_entry"])
    tool = Tool(name="create_ap_entry", description="", connector="c",
                fn=lambda a: None, side_effect=True)
    benchmark(engine.check, agent, tool, {"amount": 100}, {})


def test_bench_jwt_roundtrip(benchmark):
    secret = "bench-secret"

    def roundtrip():
        return decode_token(secret, mint_token(secret, "acme", "u1", ["builder"]))

    benchmark(roundtrip)
