## Summary

<!-- What does this change do, and why? -->

## Changes

<!-- Bullet the notable changes. -->
-

## Testing

<!-- How was this verified? Reference the tests that cover it. -->
- [ ] `pytest -q` passes locally
- [ ] `mypy bass` and `ruff check` clean
- [ ] New behavior is covered by a test

## Risk & rollout

<!-- Durability, tenancy, policy, or migration impact? Anything to watch after deploy? -->
- Schema migration: <!-- none / describe; must be backward-compatible for one release -->
- Rollback plan: <!-- Helm revision revert; note if a data migration complicates it -->

## Checklist

- [ ] Follows the fail-closed / exactly-once invariants (no new un-idempotent side effects)
- [ ] No secrets or PII added to logs or event payloads
- [ ] Docs/runbook updated if operator-facing behavior changed
