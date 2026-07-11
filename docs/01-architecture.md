# 01 · System Architecture

## 1. High-level view

Bass is built in layers. Each layer has a single responsibility and talks to the
next through a stable contract, so any one piece can be scaled or replaced
independently.

```
                          ┌─────────────────────────────────────────────┐
                          │                CLIENTS                        │
                          │  Web console · Slack/Teams bot · REST/SDK ·   │
                          │  Email inbox · Webhooks · CLI                 │
                          └───────────────────────┬─────────────────────┘
                                                  │ (authenticated)
                          ┌───────────────────────▼─────────────────────┐
                          │              API GATEWAY / EDGE               │
                          │  AuthN/AuthZ · rate limit · tenant routing    │
                          └───────────────────────┬─────────────────────┘
                                                  │
        ┌─────────────────────────────────────────┼─────────────────────────────────────────┐
        │                                         │                                          │
┌───────▼────────┐   ┌────────────────┐   ┌───────▼────────┐   ┌────────────────┐   ┌────────▼────────┐
│  TRIGGER SVC   │   │  WORKFLOW      │   │  AGENT         │   │  TOOL /        │   │  POLICY &       │
│  events, cron, │──▶│  ENGINE        │──▶│  RUNTIME       │──▶│  CONNECTOR     │   │  APPROVAL       │
│  webhooks      │   │  (orchestrator)│   │  (LLM loop)    │   │  LAYER         │   │  ENGINE         │
└────────────────┘   └───────┬────────┘   └───────┬────────┘   └───────┬────────┘   └────────┬────────┘
                             │                    │                    │                     │
                             └──────────┬─────────┴──────────┬─────────┴──────────┬──────────┘
                                        │                    │                    │
                              ┌─────────▼─────┐   ┌──────────▼────────┐  ┌────────▼─────────┐
                              │  STATE STORE  │   │  EVENT LOG /       │  │  SECRETS &       │
                              │  (Postgres)   │   │  TRACE (append)    │  │  VAULT           │
                              └───────────────┘   └───────────────────┘  └──────────────────┘
                                        │                    │
                              ┌─────────▼────────────────────▼──────────┐
                              │  KNOWLEDGE / MEMORY (vector + object)     │
                              └──────────────────────────────────────────┘
```

## 2. The layers

### Clients & ingestion
Companies interact with Bass through many surfaces: a web console for building and
monitoring automations, chat bots, a REST API + SDK for embedding automations into
their own apps, and passive channels like a dedicated email inbox or webhook URLs.

### API gateway / edge
Single entry point. Handles authentication (OIDC/SAML for humans, API keys/OAuth for
machines), authorization, per-tenant rate limiting, request validation, and routing
to the correct tenant partition.

### Trigger service
Turns the outside world into workflow runs. Three trigger classes:
- **Event** — webhook, email received, message posted, file uploaded, row changed.
- **Schedule** — cron-style ("every weekday at 9am").
- **Manual / API** — a user clicks "Run" or another system calls the API.

The trigger service is responsible for **deduplication** (idempotency keys) and
**debouncing** so the same real-world event doesn't spawn duplicate runs.

### Workflow engine (orchestrator)
The deterministic heart. A workflow is a **state machine / DAG** whose nodes are:
agent steps, tool calls, human approvals, branches/conditionals, loops, and
sub-workflows. The engine is responsible for:
- Durable execution — a run survives process restarts and resumes exactly where it left off.
- Retries with backoff, timeouts, and compensation (undo) steps.
- Idempotency so re-running a step is safe.

> **Design principle:** keep *orchestration deterministic* and confine
> *non-determinism (the LLM)* to individual steps. This is what makes an
> AI system debuggable and reliable.

### Agent runtime
Executes a single agent step: builds the prompt from role + instructions + context,
calls the model, interprets tool-use requests, and loops until the step's goal is
met or a stop condition fires (max steps, budget, timeout, error). See
[`04-agent-design.md`](04-agent-design.md).

### Tool / connector layer
Tools are the only way an agent touches the outside world. Each tool has a typed
schema, is backed by a connector (which holds the credentials), and is subject to
the policy engine before execution. Connectors are plugins.

### Policy & approval engine
The safety gate. **Every** tool call and every workflow transition is evaluated
against policy before it runs: is this tenant/agent allowed to call this tool with
these arguments? Does it exceed a budget? Does it require human approval? See
[`05-security-governance.md`](05-security-governance.md).

### State, event log, secrets, knowledge
- **State store** (Postgres): the source of truth for tenants, workflows, runs, and step state.
- **Event log** (append-only): every action, decision, cost, and output — the trace. Enables replay, audit, and debugging.
- **Secrets/vault**: credentials for connectors, encrypted per tenant, never exposed to the model.
- **Knowledge/memory**: vector store for RAG + object store for documents and artifacts.

## 3. Lifecycle of a run

```
1. Trigger fires            → Trigger service validates, dedupes, creates a Run (status=PENDING)
2. Orchestrator picks it up → loads the Workflow definition, initializes run state
3. Engine advances a step:
     a. If AGENT step   → Agent runtime executes the LLM loop
     b. If TOOL step    → Policy check → connector executes → result recorded
     c. If APPROVAL step→ Run pauses, notification sent, resumes on human decision
     d. If BRANCH       → condition evaluated, next node chosen
4. Each action appends to the event log (inputs, outputs, cost, latency, decision)
5. On completion        → Run status=SUCCEEDED/FAILED, outcome recorded, feedback hooks fire
6. On failure           → retry / compensation / escalation per workflow policy
```

Because state is durable and the log is append-only, any run can be **paused,
resumed, replayed, or forked** — essential for debugging AI behavior.

## 4. Key architectural decisions

| Decision | Rationale |
|----------|-----------|
| Deterministic engine wrapping non-deterministic steps | Reliability & debuggability; LLM chaos is contained. |
| Tools as the sole side-effect boundary | One place to enforce policy, audit, and rate limits. |
| Append-only event log as source of truth for behavior | Replayability, audit, evals, and time-travel debugging. |
| Model-agnostic runtime | Swap models per step/cost/latency; avoid vendor lock-in. |
| Human-in-the-loop as a first-class node type | Safety and trust for high-stakes actions. |
| Multi-tenant isolation at every layer | Enterprise data-security requirement. |

## 5. Technology suggestions (reference, not prescriptive)

- **Orchestration/durable execution**: Temporal, or a home-grown engine on Postgres + a queue (SQS/Redis).
- **API**: FastAPI (Python) or NestJS (TypeScript).
- **State**: PostgreSQL (+ Row-Level Security for tenant isolation).
- **Event bus / queue**: Kafka or NATS for events; SQS/Redis for work queues.
- **Vector store**: pgvector, Pinecone, or Weaviate.
- **Secrets**: HashiCorp Vault or cloud KMS.
- **Models**: Claude (Opus/Sonnet/Haiku) via the Anthropic API, selected per step by cost/capability.
- **Observability**: OpenTelemetry traces + a purpose-built run viewer.
