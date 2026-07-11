# 10 · Operations Runbook

Operational procedures for running the Bass API in production. Companion to
[07 · Deployment](07-deployment.md) (architecture) — this chapter is the
**hands-on runbook**: deploy, migrate, roll back, scale, and respond to
incidents. Deployment artifacts live in [`deploy/`](../deploy).

The API image is built, load-tested, SBOM-attested, and **cosign-signed** in CI
(`.github/workflows/ci.yml`) and published to `ghcr.io/sarmooo/bass`.

---

## Deploy

### Kubernetes (Helm)

The chart is [`deploy/helm/bass`](../deploy/helm/bass). It renders a Deployment
(2 replicas, non-root, read-only rootfs), a Service, optional Ingress + HPA, and
a **pre-upgrade migration Job** that applies schema migrations before the new
pods roll out.

```bash
# Provide the JWT secret and DB URL out-of-band (or use an existing Secret).
helm upgrade --install bass deploy/helm/bass \
  --namespace bass --create-namespace \
  --set image.tag=<sha> \
  --set secret.existingSecret=bass-prod-secret \
  --set autoscaling.enabled=true \
  --set ingress.enabled=true --set ingress.hosts[0].host=bass.example.com
```

Production secret handling: create the Secret with an operator (External
Secrets / Vault) and pass `secret.existingSecret`; do **not** put `jwtSecret`
in `values.yaml`. The Secret must carry keys `jwtSecret` and `databaseUrl`.

### Single host (docker-compose)

[`deploy/docker-compose.yml`](../deploy/docker-compose.yml) brings up
Postgres + Redis + the API, running migrations first:

```bash
export BASS_JWT_SECRET=$(openssl rand -hex 32)
docker compose -f deploy/docker-compose.yml up --build -d
curl -sf http://localhost:8000/healthz
```

### Verify a deploy

```bash
kubectl -n bass rollout status deploy/bass          # rollout completed
kubectl -n bass get job -l app.kubernetes.io/name=bass   # migrate Job succeeded
curl -sf https://bass.example.com/readyz            # datastore reachable
```

`/healthz` is liveness (process up); `/readyz` is readiness (store reachable) and
is what the load balancer and readiness probe gate on.

---

## Migrations

Schema changes are versioned and applied by `bass-migrate` (`bass.migrate`),
tracked in the `schema_migrations` table. It is **idempotent** — safe to run on
every deploy — and **transactional** — a failure leaves the DB on the last good
version.

```bash
# Run standalone (the Helm chart runs this automatically as a pre-upgrade hook):
BASS_DB_URL=postgresql://user:pass@host:5432/bass bass-migrate
```

Migrations run **before** new code so the old replicas keep working against the
new schema; keep every migration backward-compatible for one release (expand →
migrate → contract). The baseline enables row-level security — the app role must
be a **non-superuser without BYPASSRLS** for RLS to bind.

---

## Roll back

Application rollback is a Helm revision revert:

```bash
helm -n bass history bass
helm -n bass rollback bass <previous-revision>
```

Because migrations are expand-then-contract and backward-compatible for one
release, rolling the app back one version is safe without a schema rollback.
Never data-destructive-migrate and app-deploy in the same release — split them so
either half can revert independently.

Image provenance before promoting a tag:

```bash
cosign verify ghcr.io/sarmooo/bass@<digest> \
  --certificate-identity-regexp "https://github.com/sarmooo/Bass-platform-/.*" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

---

## Scale

- **API replicas** — stateless; scale horizontally. Enable the HPA
  (`autoscaling.enabled=true`, CPU target 70%) or set `replicaCount`.
- **Database** — the bottleneck under write load (each step checkpoints). Scale
  vertically first, then add read replicas for the console's list/trace reads,
  then partition the `events` table by time.
- **Rate limit** — per-tenant, `BASS_RATE_LIMIT_PER_MIN` (default 1000/min).
  Back it with Redis (`RedisRateLimiter`) so the window is shared across
  replicas; the in-memory limiter is per-replica.
- **Durable execution** — for high fan-out, drive runs through the Temporal
  adapter (`bass.temporal_adapter`) and scale the worker fleet.

Capacity signal: the CI k6 load-smoke (10 VUs firing end-to-end runs) is the
lower bound; run a fuller k6 profile against staging before a launch.

---

## Observe

- **Logs** — structured JSON (`BASS_LOG_JSON=1`); each line carries the run's
  trace id.
- **Metrics** — `GET /metrics` (authenticated) exposes counters
  (`runs.succeeded`, `runs.failed{reason}`, `tool.calls`, `policy.decisions`,
  `steps.retries`, `runs.paused`).
- **Tracing** — OpenTelemetry spans per run and per step when the `[otel]` extra
  and an OTLP endpoint are configured (`bass.tracing`).
- **Audit** — the per-run event log in the store is the durable record; every
  policy decision, approval, and tool result is captured (payloads redacted).

Suggested alerts: `runs.failed{reason=connector}` rate spike (dependency down),
`/readyz` failing (DB unreachable), 429 rate climbing (a tenant hitting its
limit), migration Job failing (blocks rollout).

---

## Incident response

| Symptom | First checks | Action |
|---------|-------------|--------|
| `/readyz` 503 | DB reachable? connection pool exhausted? | Restore DB connectivity; the API recovers without data loss — runs resume from their durable cursor. |
| Rollout stuck | migrate Job status/logs | Fix the migration or DB; the pre-upgrade hook intentionally blocks a bad rollout. |
| Runs stuck `waiting_approval` | expected? approver notified? | Resolve via `POST /v1/runs/{id}/approvals/{aid}` or the console; a canceled run posts nothing. |
| Spike in `runs.failed{connector}` | dependency health; circuit breakers open? | The breaker sheds load automatically; fix upstream, breakers half-open on cooldown. |
| Duplicate side effects suspected | idempotency ledger (`tool_effects`) | By design a replay returns the recorded result; check the run's trace for `tool_result_replayed`. |
| Sudden 429s | which tenant? limit right-sized? | Raise `BASS_RATE_LIMIT_PER_MIN` or move the tenant to a higher tier. |

**Crash recovery is automatic.** A pod that dies mid-run loses nothing: the run's
cursor, context, cost, and events were checkpointed after the last completed
step, and mutating steps are guarded by the idempotency ledger. Restart (or let
Kubernetes reschedule) and the run resumes exactly-once from where it stopped —
this is the property proven by `tests/test_chaos.py`.

---

## Backup & restore

PostgreSQL is the source of truth (runs, events, approvals, idempotency ledger).

- **Backups** — managed PITR (e.g. cloud snapshots + WAL archiving). Target RPO
  ≤ 5 min; the event log makes runs replayable but not re-runnable, so protect it.
- **Restore drill** — restore to a scratch instance, point `BASS_DB_URL` at it,
  run `bass-migrate` (no-op if current), and hit `/readyz`.
- **Retention** — `store.purge_event_payloads` empties event payloads past a
  retention horizon while keeping cost/audit rows; schedule it per tenant policy.
