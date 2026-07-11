# Bass — Enterprise AI Automation Platform

**Bass** is a reference design for a comprehensive AI automation platform that lets
companies automate repetitive, knowledge-heavy, and cross-system work using AI
agents, deterministic workflows, and human oversight — safely and at scale.

This repository is a **design + working reference implementation**. It contains the
architecture, data model, and security model (in [`docs/`](docs/)), plus a
production-shaped Python implementation of the control loop (in [`reference/`](reference/))
that is continuously verified in CI.

## Implemented & CI-verified

The [`reference/`](reference/) code is not a toy scaffold — it is exercised on every
push by a multi-job pipeline ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)):
lint + types + unit tests with a coverage gate, an **integration suite against a real
PostgreSQL** service container, a **Temporal** durable-execution test on Temporal's
test server, and a **Docker image build → smoke test → publish to GHCR**.

| Capability | Where | Verified by |
|---|---|---|
| Durable, crash-safe, idempotent execution | `workflow.py`, `store.py` | crash→resume→exactly-once test |
| Durable execution on **Temporal** | `temporal_adapter.py` | Temporal test-server job |
| State layer: SQLite **and PostgreSQL** | `store.py`, `store_postgres.py` | same suite on both backends |
| DB-enforced tenant isolation (**RLS**) | `store_postgres.py` | real-Postgres RLS test |
| Fail-closed policy gate + async approvals | `policy.py`, `workflow.py` | policy + API approval tests |
| **HTTP API** + JWT auth + RBAC | `api.py`, `auth.py` | FastAPI TestClient suite |
| Connector reliability + real HTTP connector | `connectors.py` | live local-server test |
| Scheduled (cron) triggers | `schedule.py` | cron + dedup tests |
| Run cancellation, retention/purge, readiness, metrics, list | `store*.py`, `api.py` | dedicated tests |
| Real model provider (Claude) | `providers.py` | native tool-use (opt-in) |
| Packaging, Docker→GHCR, mypy, coverage, pip-audit, Dependabot | root + `pyproject.toml` | CI |

See [`reference/README.md`](reference/README.md) for how to run each. External
infrastructure sits behind interfaces (SQLite→Postgres, MockModel→Claude,
env-secrets→vault, in-process→Temporal) so a deployment swaps implementations without
touching the engine.

---

## What problem does it solve?

Most companies have hundreds of small, tedious processes glued together by people:
triaging tickets, reconciling invoices, onboarding employees, answering internal
questions, updating CRMs, generating reports. Each is individually small but
collectively expensive, error-prone, and slow.

Bass lets a company describe these processes once and then run them automatically:

- **Triggered** by events (a new email, a Slack message, a webhook, a schedule).
- **Executed** by AI agents that can reason, call tools, and read/write systems.
- **Governed** by policies, approvals, budgets, and full audit trails.
- **Improved** over time from outcome feedback and evaluations.

## Design goals

| Goal | How Bass achieves it |
|------|----------------------|
| **Reliability** | Deterministic workflow engine wraps non-deterministic AI steps; retries, idempotency keys, and compensation. |
| **Safety** | Every tool call passes a policy gate; risky actions require human approval; per-tenant budgets and rate limits. |
| **Observability** | Every run is a traced, replayable event log. Costs, latency, and outcomes are first-class. |
| **Extensibility** | Tools, triggers, and models are plugins behind stable interfaces. |
| **Multi-tenancy** | Hard isolation of data, secrets, and compute per company. |
| **Human-in-the-loop** | Approvals, escalations, and overrides are built into the core loop, not bolted on. |

## The five core concepts

1. **Connector** — an authenticated integration to an external system (Gmail, Slack, Salesforce, a database, an internal API).
2. **Tool** — a single capability exposed to an agent (`send_email`, `create_ticket`, `query_db`), backed by a connector.
3. **Agent** — an LLM-driven worker with a role, instructions, a tool allow-list, and guardrails.
4. **Workflow** — a deterministic orchestration (DAG / state machine) that sequences agents, tools, approvals, and branches.
5. **Trigger** — the event or schedule that starts a workflow run.

A **Run** is one execution of a workflow, and everything about it is recorded.

## Repository layout

```
.
├── README.md                     ← you are here
├── docs/
│   ├── 01-architecture.md         System architecture & request lifecycle
│   ├── 02-components.md           Component-by-component deep dive
│   ├── 03-data-model.md           Entities, schemas, storage
│   ├── 04-agent-design.md         Agent loop, prompting, tool use, memory
│   ├── 05-security-governance.md  Auth, isolation, policy, compliance
│   ├── 06-observability.md        Tracing, evals, cost, feedback loops
│   ├── 07-deployment.md           Infra, scaling, environments
│   ├── 08-use-cases.md            Worked end-to-end examples
│   └── 09-roadmap.md              Phased delivery plan
└── reference/                    Production-shaped Python implementation
    ├── README.md
    ├── pyproject.toml            Packaging, extras ([live], [dev]), ruff/pytest config
    ├── bass/
    │   ├── config.py             Env-driven settings
    │   ├── errors.py             Typed error hierarchy
    │   ├── observability.py      Structured JSON logging + metrics
    │   ├── redaction.py          Secret/PII masking at write time
    │   ├── models.py             Core domain types
    │   ├── store.py              Store interface + SQLiteStore (durable, ACID, tenant-scoped)
    │   ├── secrets.py            SecretsProvider interface + env/static impls
    │   ├── policy.py             Fail-closed, deny-by-default policy engine
    │   ├── budgets.py            Cost/step ceilings
    │   ├── connectors.py         Timeout · retries · circuit breaker
    │   ├── tools.py              Tool registry + connector-backed tools + MockModel
    │   ├── providers.py          Real model provider — Claude via the Anthropic API
    │   ├── agent.py              The agent control loop
    │   ├── workflow.py           Durable, crash-safe, idempotent engine
    │   ├── orchestrator.py       Trigger dedup → run → resume
    │   ├── example_run.py        Worked end-to-end example (offline)
    │   └── example_run_live.py   Same pipeline, driven by a real Claude call
    └── tests/                    21-test suite (crash-resume, isolation, dedup, policy, …)
```

## Where to start reading

- **Executives / product**: [`docs/08-use-cases.md`](docs/08-use-cases.md) then [`docs/09-roadmap.md`](docs/09-roadmap.md).
- **Architects**: [`docs/01-architecture.md`](docs/01-architecture.md).
- **Engineers**: [`docs/02-components.md`](docs/02-components.md) and the [`reference/`](reference/) implementation.
- **Security / compliance**: [`docs/05-security-governance.md`](docs/05-security-governance.md).

## Quick start (reference implementation)

```bash
cd reference
python -m bass.example_run                      # run the durable invoice-triage pipeline
python -m unittest discover -s tests -t .       # run the 21-test suite (stdlib only)
```

The example fires an "invoice triage" trigger, persists a durable run to SQLite,
and prints the traced event log read back from the store — policy decisions, the
approval gate, and the final result.

The [`reference/`](reference/) code is a **production-shaped** implementation of
the control loop: durable, crash-safe, idempotent execution; a fail-closed policy
gate; connector reliability; redaction; enforced tenant isolation; and budgets —
with external infrastructure behind swappable interfaces (SQLite→Postgres,
MockModel→Claude, env-secrets→vault). See [`reference/README.md`](reference/README.md).
