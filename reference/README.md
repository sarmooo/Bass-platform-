# Bass — Reference Implementation

A compact but **production-shaped** implementation of the Bass core: durable,
crash-safe, idempotent workflow execution with a fail-closed policy gate,
connector reliability, secret/PII redaction, enforced tenant isolation, budgets,
and structured observability.

External infrastructure sits behind interfaces with working defaults, so a
deployment swaps the implementation without touching the engine:

| Concern | Interface | Reference default | Production swap |
|---------|-----------|-------------------|-----------------|
| State + event log | `store.Store` | `SQLiteStore` (WAL, ACID) | `PostgresStore` (implemented; CI-verified) |
| Model | `respond()` | `tools.MockModel` | `providers.AnthropicModel` (Claude) |
| Execution driver | `engine.resume` | in-process | `temporal_adapter` (Temporal workflow) |
| Secrets | `secrets.SecretsProvider` | `EnvSecrets` | Vault / cloud KMS |
| Connector | `connectors.Connector` | in-process example | Gmail, Salesforce, HTTP, DB |
| Metrics | `observability.Metrics` | in-process counters | OpenTelemetry |

## What's implemented (and tested)

- **Durable, crash-safe execution** (`workflow.py`, `store.py`) — a run's cursor,
  context, cost, and new events advance in a single transaction after each step.
  A process that dies mid-run resumes from the last committed cursor.
- **Exactly-once side effects** — mutating tool steps consult an idempotency
  ledger before acting and record their result after, so a crash-replay returns
  the recorded result instead of acting twice.
- **Fail-closed policy gate** (`policy.py`) — least-privilege allow-lists,
  declarative versioned rules, deny-by-default for mutations, and denial when a
  rule itself errors.
- **Trigger deduplication** (`orchestrator.py`) — a redelivered event maps to the
  same run via a stable idempotency key.
- **Durable human approval** — high-value actions pause for a recorded decision.
- **Connector reliability** (`connectors.py`) — timeout, bounded retries with
  backoff, and a circuit breaker.
- **Redaction at write time** (`redaction.py`) — secrets/PII are masked before
  anything is persisted or logged.
- **Enforced tenant isolation** (`store.py`) — every read/write is tenant-scoped;
  a cross-tenant access raises rather than leaking.
- **Budgets** (`budgets.py`) — cost and step ceilings fail a run closed.
- **Structured logging + metrics** (`observability.py`).
- **Property-based invariants** (`tests/test_properties.py`) — Hypothesis checks
  the core guarantees across thousands of generated inputs: redaction never
  leaks a secret/email/API key and is idempotent, the rate limiter admits
  exactly `min(calls, limit)` per window, JWT mint/decode round-trips and rejects
  a wrong secret, and the cron matcher is correct.
- **OpenTelemetry tracing** (`tracing.py`) — a span per run and per step (attributes
  `bass.run_id`, `bass.status`, `bass.node_type`, …); no-op unless the `[otel]` extra
  is installed. Verified with an in-memory exporter in `tests/test_otel.py`.
- **Real HTTP connector** (`connectors.http_call`) — retries 5xx/connection
  errors as transient, returns 4xx; integration-tested against a live local
  server (`tests/test_connectors_http.py`).
- **Outbound webhook connector** (`webhook.py`) — a concrete Slack-style
  notification integration on the reliable HTTP connector; a side-effect tool
  (idempotency key forwarded as a header, routed through the ledger + policy
  gate). Contract-tested against a live local server (`tests/test_webhook.py`).
- **DB-enforced tenant isolation** — PostgreSQL row-level security
  (`store_postgres.apply_row_level_security` / `RLS_SQL`), proven at the database
  layer in `tests/test_rls.py`.
- **Concurrency-safe** — exactly-once under contention (32 concurrent fires of one
  trigger → one run, one side effect) in `tests/test_concurrency.py`.
- **Scheduled (cron) triggers** (`schedule.py`) — a stdlib 5-field cron matcher and
  a scheduler whose firings dedup to once-per-minute across replicas; completes the
  event/manual/schedule trigger model. Tested in `tests/test_schedule.py`.
- **Temporal durable execution** (`temporal_adapter.py`) — a run driven by a real
  Temporal workflow; verified against Temporal's test server in CI.
- **Per-tenant rate limiting** (`ratelimit.py`) — a fixed-window limiter guards
  every fire; over-limit returns HTTP 429. Process-local `InMemoryRateLimiter` by
  default, `RedisRateLimiter` (INCR + EXPIRE) for a window shared across instances,
  behind one `RateLimiter` interface. Both are tested — the Redis path against a
  real Redis service container in CI (`tests/test_ratelimit.py`).

## Run the offline example

```bash
cd reference
python -m bass.example_run
```

Fires an invoice-triage trigger, persists to SQLite, and prints the durable trace
read back from the store. The $8,200 invoice exceeds the $5,000 threshold, so
posting the AP entry pauses for approval.

## Run the tests

```bash
cd reference
python -m unittest discover -s tests -t .      # zero dependencies
# or, with dev tooling:  pip install -e ".[dev]" && pytest -q
```

21 tests cover the properties above, including a crash-before-checkpoint →
resume → **exactly-once** scenario and cross-tenant isolation. CI runs the
identical suite twice — once on SQLite and once against a **real PostgreSQL**
service container (`bass.store_postgres.PostgresStore`), proving the state layer
on production infrastructure. To reproduce locally:

```bash
export BASS_TEST_DB_URL=postgresql://user:pass@localhost:5432/bass
pip install -e ".[dev,postgres]" && pytest -q
```

## Run the HTTP API

```bash
cd reference
pip install -e ".[api]"
export BASS_JWT_SECRET=dev-secret BASS_DB_PATH=bass.db
uvicorn --factory bass.api:build_default_app
```

Open `http://localhost:8000/` for the **operator console** (`dashboard.py`) — a
self-contained web UI to list runs, read traces, and approve/cancel, talking to
the same JSON endpoints below.

A FastAPI service over the engine: `POST /v1/workflows/{name}/runs` (fire),
`GET /v1/runs/{id}` and `/trace`, and `POST /v1/runs/{id}/approvals/{approval_id}`
(resolve). Every request runs under a bearer-JWT `Principal`; the `tenant_id`
scopes all data access and RBAC roles gate mutating endpoints. Approvals are
**asynchronous** — firing the $8,200 invoice returns `waiting_approval`, and the
approval endpoint resolves it and resumes the run. Covered by `tests/test_api.py`
(auth, RBAC, the async approval round-trip, and tenant isolation).

## Run the live example (real Claude call)

```bash
cd reference
pip install -e ".[live]"
export ANTHROPIC_API_KEY=sk-ant-...            # or: ant auth login
python -m bass.example_run_live
```

Same durable pipeline, but the extractor agent is driven by
`providers.AnthropicModel` — real native tool use, structured JSON output, and
per-model cost roll-up. Makes a real, billed API call.

## Container & CI

A root `Dockerfile` builds a slim, non-root image that serves the API
(`uvicorn --factory bass.api:build_default_app`). CI (`.github/workflows/ci.yml`)
runs four jobs on every push: **unit** (ruff + mypy + pytest with an 80% coverage
gate + `pip-audit`), **integration** (the suite against a real PostgreSQL
container plus a real Redis container for the distributed rate limiter),
**temporal** (a run driven by Temporal's test server), and **docker** (build the
image, smoke-test `/healthz`, run a **k6 load-smoke** — 10 VUs firing authenticated
runs with p95/error-rate gates — then publish to GHCR with an **SBOM + SLSA
provenance** attestation and a **cosign keyless signature**, verified in-workflow).
Dependabot keeps pip and Actions dependencies current.

## Deploy it

Deployment artifacts live in [`../deploy`](../deploy) and are validated in CI
(`helm lint`/`template`/kubeconform + `docker compose config`):

- **docker-compose** — Postgres + Redis + the API on one host, schema migrations
  applied first: `docker compose -f deploy/docker-compose.yml up --build`.
- **Helm chart** (`deploy/helm/bass`) — a non-root Deployment, Service, optional
  Ingress + HPA, and a **pre-upgrade migration Job**.
- **Schema migrations** (`bass.migrate` / `bass-migrate`) — versioned,
  transactional, idempotent; baseline schema + row-level security, tracked in
  `schema_migrations`. Tested in `tests/test_migrate.py`.

The [operations runbook](../docs/10-operations.md) covers deploy, migrate, roll
back, scale, incident response, and backup/restore. The
[supply-chain & governance](../docs/11-supply-chain-and-governance.md) chapter
covers the PR → signed release → gated deploy pipeline (required checks,
CODEOWNERS, Trivy image scan, cosign verification, the `production` environment
approval gate) and the settings that live in repo admin.

## What still requires real infrastructure

This is a faithful single-process implementation of the control loop. A full
enterprise deployment additionally needs: managed Postgres with RLS (swap
`Store`), a durable-execution backend or worker fleet for horizontal scale, a
KMS-backed vault (swap `SecretsProvider`), real SaaS connectors, an API gateway
with SSO/RBAC, and an evaluation + observability stack. Each is a documented swap
behind the interfaces above — see `../docs/07-deployment.md`.
