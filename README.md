# Bass — Enterprise AI Automation Platform

**Bass** is a reference design for a comprehensive AI automation platform that lets
companies automate repetitive, knowledge-heavy, and cross-system work using AI
agents, deterministic workflows, and human oversight — safely and at scale.

This repository is a **design + reference implementation**. It contains the
architecture, data model, security model, and a runnable Python scaffold that
demonstrates the core control loop.

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
└── reference/                    Runnable Python scaffold of the core loop
    ├── README.md
    ├── requirements.txt
    └── bass/
        ├── models.py             Core domain types
        ├── policy.py             Policy engine (the safety gate)
        ├── tools.py              Tool registry + example tools
        ├── agent.py              The agent control loop
        ├── workflow.py           Deterministic workflow engine
        ├── orchestrator.py       Ties triggers → workflows → runs
        └── example_run.py        A worked end-to-end example
```

## Where to start reading

- **Executives / product**: [`docs/08-use-cases.md`](docs/08-use-cases.md) then [`docs/09-roadmap.md`](docs/09-roadmap.md).
- **Architects**: [`docs/01-architecture.md`](docs/01-architecture.md).
- **Engineers**: [`docs/02-components.md`](docs/02-components.md) and the [`reference/`](reference/) scaffold.
- **Security / compliance**: [`docs/05-security-governance.md`](docs/05-security-governance.md).

## Quick start (reference scaffold)

```bash
cd reference
python -m bass.example_run
```

This runs a simulated "invoice triage" workflow end-to-end — no external API keys
required — printing the traced event log, policy decisions, and the final result.
