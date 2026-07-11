# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims to
follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Releases are
drafted automatically from merged PRs (release-drafter) and published from a
`v*` tag.

## [Unreleased]

### Added
- Durable, crash-safe workflow engine with exactly-once side effects (idempotency
  ledger), fail-closed policy gate, budgets, and human approvals.
- State layer: `SQLiteStore` and `PostgresStore` (with row-level security),
  proven on real PostgreSQL in CI.
- HTTP API (FastAPI) with bearer-JWT auth + RBAC, operator console, per-tenant
  rate limiting, and a committed OpenAPI spec (drift-checked).
- Connector reliability (timeout/retry/circuit breaker), a real HTTP connector,
  and a Slack-style webhook connector.
- OpenTelemetry tracing, structured logging, metrics; scheduled (cron) triggers;
  Temporal durable-execution adapter.
- Versioned schema migrations (`bass-migrate`).
- Testing: unit, integration (Postgres/Redis/Temporal), property-based (Hypothesis),
  chaos/failure-injection, concurrency, and end-to-end (docker-compose + kind).
- Supply chain: multi-arch image with SBOM + SLSA provenance, cosign signing,
  Trivy/bandit/gitleaks/pip-audit, Dependabot.
- Deployment: docker-compose, a Helm chart (values schema + unit tests) with a
  pre-upgrade migration hook, a Terraform module, and an operations runbook.

[Unreleased]: https://github.com/sarmooo/Bass-platform-/commits/main
