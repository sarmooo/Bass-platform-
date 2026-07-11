# 07 · Deployment & Operations

## Environments

| Env | Purpose |
|-----|---------|
| **dev** | Local scaffold + mocked connectors/models for fast iteration. |
| **staging** | Full stack, sandbox connector credentials, runs the eval suite. |
| **prod** | Live, multi-tenant, with canary + progressive rollout. |

Workflows and agents are **versioned artifacts** promoted dev → staging → prod. A run
always pins the version it executed, so promotion never rewrites history.

## Service topology

Bass decomposes into independently scalable services:

```
api-gateway          stateless, horizontally scaled behind a load balancer
trigger-service      consumers for webhooks/email/queues + a scheduler
orchestrator         durable workflow engine workers (e.g. Temporal workers)
agent-runtime        LLM-loop workers; scale with LLM concurrency, isolate per tenant tier
tool-executor        connector calls; sandboxed, per-connector circuit breakers
policy-engine        low-latency decision service (cacheable, versioned rules)
web-console          the builder/monitor UI
```

Backing stores: PostgreSQL (state, RLS), an event log (partitioned table or Kafka),
a queue (SQS/Redis/NATS), a vector store (pgvector/Pinecone), object storage
(artifacts), and a secrets vault.

## Scaling strategy

- **Stateless services** (gateway, agent-runtime, tool-executor) scale horizontally
  on CPU/queue-depth; they hold no session state.
- **Orchestrator** scales by adding durable-execution workers; work is partitioned by
  run/tenant.
- **Concurrency isolation:** per-tenant and per-tier concurrency limits so one busy
  tenant can't starve others (bulkheads).
- **Model throughput:** a model-gateway pools provider capacity, handles rate limits,
  retries, and routes across models/regions.
- **Backpressure:** when downstream is saturated, triggers queue rather than drop, and
  over-budget tenants are shed first.

## Reliability

- **Durable execution:** runs survive worker restarts and resume from committed state.
- **Idempotency:** every mutating tool call carries an idempotency key so retries and
  replays don't double-act.
- **Circuit breakers & timeouts** per connector; a flaky vendor API degrades one tool,
  not the platform.
- **Graceful degradation:** if a model/provider is down, the model-gateway fails over;
  if policy is unavailable, actions fail closed and escalate.
- **DR:** Postgres PITR backups, replicated event log, infrastructure as code for
  region rebuild; target RPO/RTO documented per plan tier.

## Security operations

- Secrets in a vault with rotation; short-lived tokens minted per tool call.
- Network egress allow-lists for connectors.
- Continuous secret scanning and dependency scanning in CI.
- Audit-log shipping to the tenant's SIEM on enterprise plans.

## CI/CD

```
commit ─▶ lint + unit tests ─▶ agent/workflow evals ─▶ build images
      ─▶ deploy staging ─▶ integration + eval gates ─▶ canary prod ─▶ progressive rollout
```

Prompt, agent, and workflow changes are code: reviewed, eval-gated, and rolled out
progressively with automatic rollback on metric/eval regression.

## Deployment options

- **Multi-tenant SaaS** — default; hard logical isolation per tenant.
- **Single-tenant / dedicated** — isolated compute and datastore for regulated customers.
- **VPC / self-hosted** — the whole stack in the customer's cloud account for maximum
  data control; the model can be reached via the customer's own provider account or a
  private endpoint.
