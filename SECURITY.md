# Security Policy

## Reporting a vulnerability

Please report vulnerabilities **privately** via a GitHub security advisory:
<https://github.com/sarmooo/Bass-platform-/security/advisories/new>. Do not open
a public issue for a security problem.

Include repro steps and impact. We aim to acknowledge within a few business days.

## Scope

This is a reference implementation. The security-relevant properties it upholds —
and that a report should hold it to — are:

- **Tenant isolation** — no cross-tenant read/write (app-level checks + PostgreSQL
  row-level security).
- **Fail-closed policy** — mutations are deny-by-default; a rule that errors denies.
- **Exactly-once side effects** — the idempotency ledger prevents double-execution.
- **Secret/PII redaction** — secrets are masked before anything is persisted or logged.
- **AuthN/AuthZ** — bearer-JWT principal, RBAC on mutating endpoints.

## Handling in this project

- Dependencies: Dependabot + `pip-audit` (source) and Trivy (image).
- Static analysis: `bandit`, `ruff`, `mypy`, CodeQL (repo setting).
- Secret scanning: `gitleaks` in CI plus GitHub push protection (recommended).
- Releases: images are cosign-signed with an SBOM + SLSA provenance; verify before
  deploying (see `docs/11-supply-chain-and-governance.md`).

## Deploying securely

Run under a non-superuser database role (so RLS binds), manage the JWT secret and
DB URL via a secrets manager (not `values.yaml`), keep the container non-root with
a read-only root filesystem (the chart defaults to this), and gate production
deploys behind the `production` environment's required reviewers.
