# Contributing

Thanks for your interest in Bass. This is a reference implementation with a
strong emphasis on **provable correctness** — changes are held to the platform's
invariants, not just "tests pass".

## Development

```bash
cd reference
pip install -e ".[dev,api,postgres,otel]"
pytest -q                 # full suite (SQLite)
ruff check bass tests     # lint
mypy bass                 # types
```

CI additionally runs the suite on Python 3.11–3.13, against real PostgreSQL +
Redis + Temporal, plus property-based, chaos, and end-to-end (compose + kind)
tests. Reproduce the Postgres path locally with
`BASS_TEST_DB_URL=postgresql://… pytest -q`.

## Invariants (do not regress)

- **Exactly-once side effects** — any new mutating tool goes through the
  idempotency ledger; a crash-replay must not act twice.
- **Fail-closed policy** — mutations are deny-by-default; a rule that errors denies.
- **Tenant isolation** — every store read/write is tenant-scoped; nothing crosses
  the boundary (RLS enforces this at the DB too).
- **Redaction at write time** — no secret/PII reaches storage or logs.
- **Determinism in the engine** — keep non-determinism (the model) inside agent
  nodes; the workflow driver must stay replayable.

## Pull requests

1. Branch, make focused commits.
2. Add or extend a test that would fail without your change (`test_chaos.py` and
   `test_properties.py` are good models for durability/invariant coverage).
3. Update the OpenAPI spec if you touched a route: `python -m scripts.export_openapi`.
4. Update `docs/` / the runbook if operator-facing behavior changed.
5. Fill in the PR template (it lists the invariant and rollback checks).

## Migrations

Schema changes are versioned in `bass/migrate.py` and must be
backward-compatible for one release (expand → migrate → contract) so a rollback
never needs a schema rollback.

## Security

Report vulnerabilities privately — see [SECURITY.md](SECURITY.md).
