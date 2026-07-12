"""Property-based tests (Hypothesis) for the core invariants.

The example-based suites pin specific cases; these assert the *properties* those
cases are instances of, checked across thousands of generated inputs:

- redaction never leaves a secret-keyed value, an email, or an API key in the
  clear, and is idempotent;
- the rate limiter admits exactly min(calls, limit) per window, per key;
- JWT mint/decode round-trips and rejects the wrong secret;
- the cron matcher's minute field matches iff the minute is equal, and `*`
  always matches.

Requires the `[dev]` extra (hypothesis). If it isn't installed the module
degrades to a single skipped test, so the zero-dependency unittest path still
runs.
"""

from __future__ import annotations

import json
import unittest

try:
    from hypothesis import assume, given, settings
    from hypothesis import strategies as st
    _HAVE_HYP = True
except ImportError:
    _HAVE_HYP = False

from bass.auth import ROLES, AuthError, decode_token, mint_token
from bass.ratelimit import InMemoryRateLimiter
from bass.redaction import REDACTED, redact
from bass.schedule import _parse_field, cron_matches


if _HAVE_HYP:
    _fixed_clock = (lambda: 1000.0)                      # one window, deterministic

    _name = st.text(
        alphabet=st.characters(min_codepoint=33, max_codepoint=126),
        min_size=1, max_size=40)
    _roles = st.lists(st.sampled_from(list(ROLES)), unique=True)
    _alnum = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789",
                     min_size=1, max_size=12)

    _secret_keys = st.sampled_from(
        ["password", "passwd", "api_key", "apiKey", "secret", "token",
         "authorization", "private_key", "credential"])

    _leaves = st.one_of(st.none(), st.booleans(), st.integers(),
                        st.text(max_size=20))
    _values = st.recursive(
        _leaves,
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(st.text(max_size=6), children, max_size=4)),
        max_leaves=25)

    class RedactionProperties(unittest.TestCase):
        @given(key=_secret_keys, val=st.text(min_size=1), amount=st.integers())
        def test_secret_key_always_masked_nonsecret_preserved(self, key, val, amount):
            out = redact({key: val, "amount": amount})
            self.assertEqual(out[key], REDACTED)
            self.assertEqual(out["amount"], amount)      # non-secret scalar untouched

        @given(_values)
        @settings(max_examples=200)
        def test_redaction_is_idempotent(self, value):
            once = redact(value)
            self.assertEqual(redact(once), once)

        @given(local=_alnum, domain=_alnum, tld=st.sampled_from(["com", "org", "io", "net"]))
        def test_emails_are_masked(self, local, domain, tld):
            email = f"{local}@{domain}.{tld}"
            out = redact({"note": f"reach {email} today"})
            self.assertNotIn(email, out["note"])
            self.assertIn(REDACTED, out["note"])

        @given(st.text(alphabet="ABCDEFabcdef0123456789_-", min_size=8, max_size=40))
        def test_anthropic_keys_are_masked(self, tail):
            key = "sk-ant-" + tail
            out = redact({"msg": f"the key is {key} ok"})
            self.assertNotIn(key, out["msg"])

        @given(_values)
        def test_output_is_json_serializable(self, value):
            json.dumps(redact(value))                     # redaction never breaks persistence

    class RateLimiterProperties(unittest.TestCase):
        @given(n=st.integers(min_value=0, max_value=60),
               limit=st.integers(min_value=1, max_value=25))
        def test_admits_exactly_min_calls_limit(self, n, limit):
            rl = InMemoryRateLimiter(limit=limit, window_s=60.0, clock=_fixed_clock)
            allowed = sum(1 for _ in range(n) if rl.allow("tenant"))
            self.assertEqual(allowed, min(n, limit))

        @given(a=st.integers(0, 15), b=st.integers(0, 15),
               limit=st.integers(min_value=1, max_value=8))
        def test_keys_are_independent(self, a, b, limit):
            rl = InMemoryRateLimiter(limit=limit, window_s=60.0, clock=_fixed_clock)
            allowed_a = sum(1 for _ in range(a) if rl.allow("a"))
            allowed_b = sum(1 for _ in range(b) if rl.allow("b"))
            self.assertEqual(allowed_a, min(a, limit))
            self.assertEqual(allowed_b, min(b, limit))

    class AuthProperties(unittest.TestCase):
        @given(tenant=_name, subject=_name, roles=_roles, secret=_name)
        def test_mint_decode_roundtrip(self, tenant, subject, roles, secret):
            principal = decode_token(secret, mint_token(secret, tenant, subject, roles))
            self.assertEqual(principal.tenant_id, tenant)
            self.assertEqual(principal.subject, subject)
            self.assertEqual(principal.roles, roles)

        @given(tenant=_name, subject=_name, roles=_roles, s1=_name, s2=_name)
        def test_wrong_secret_is_rejected(self, tenant, subject, roles, s1, s2):
            assume(s1 != s2)
            token = mint_token(s1, tenant, subject, roles)
            with self.assertRaises(AuthError):
                decode_token(s2, token)

    class CronProperties(unittest.TestCase):
        @given(minute=st.integers(0, 59), dt=st.datetimes())
        def test_minute_field_matches_iff_equal(self, minute, dt):
            self.assertEqual(cron_matches(f"{minute} * * * *", dt), dt.minute == minute)

        @given(st.datetimes())
        def test_wildcard_always_matches(self, dt):
            self.assertTrue(cron_matches("* * * * *", dt))

        @given(a=st.integers(0, 59), b=st.integers(0, 59))
        def test_range_parse_is_the_inclusive_range(self, a, b):
            lo, hi = min(a, b), max(a, b)
            self.assertEqual(_parse_field(f"{lo}-{hi}", 0, 59), set(range(lo, hi + 1)))

else:
    class HypothesisUnavailable(unittest.TestCase):
        @unittest.skip("hypothesis not installed ([dev] extra)")
        def test_property_based_suite(self):
            pass


if __name__ == "__main__":
    unittest.main()
