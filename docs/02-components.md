# 02 · Components — Deep Dive

This chapter walks each subsystem: its responsibility, interface, and the design
choices that make it robust.

---

## Trigger Service

**Responsibility:** convert external events and schedules into deduplicated
workflow runs.

**Interface (conceptual):**
```
POST /triggers/{trigger_id}/fire      # webhook / API
Schedule: cron expression per trigger  # scheduler
Listener: email, chat, queue consumers # push channels
```

**Key mechanics:**
- **Idempotency key** derived from the source event (e.g. email Message-ID, webhook delivery ID). Duplicate deliveries map to the same run.
- **Debounce / batching** windows for chatty sources (e.g. collapse 20 file-change events in 5s into one run).
- **Filtering**: a trigger declares a match predicate so only relevant events start a run.
- **Backpressure**: if a tenant is over budget or rate limit, triggers are queued or rejected with a clear reason.

---

## Workflow Engine

**Responsibility:** durably execute a workflow definition as a sequence/graph of steps.

**Workflow definition** (declarative — YAML/JSON authored in the console or via SDK):
```yaml
name: invoice-triage
trigger: { type: email, match: { to: "invoices@acme.com" } }
budget: { max_usd: 0.50, max_steps: 25 }
steps:
  - id: extract
    type: agent
    agent: invoice_extractor
    input: "{{ trigger.email }}"
  - id: validate
    type: tool
    tool: lookup_vendor
    args: { vendor: "{{ steps.extract.output.vendor }}" }
  - id: decide
    type: branch
    when:
      - if: "{{ steps.extract.output.amount > 5000 }}"
        goto: approval
      - else: goto: post
  - id: approval
    type: approval
    approvers: ["finance-team"]
    timeout: 24h
    on_reject: goto: notify_rejection
  - id: post
    type: tool
    tool: create_ap_entry
    args: { ... }
```

**Node types:** `agent`, `tool`, `approval`, `branch`, `loop`, `sub_workflow`,
`wait` (until a time/event), `transform` (pure data mapping), `terminate`.

**Durability model:** each step transition is committed to the state store before
executing side effects; on crash the engine replays from the last committed state.
Steps must be **idempotent** (via idempotency keys passed to connectors) so a
replay after a partial failure doesn't double-act.

**Failure handling per step:**
- `retry`: N attempts, exponential backoff, optional jitter.
- `catch`: route to a handler step on error.
- `compensate`: run an undo step for already-completed side effects (saga pattern).
- `escalate`: page a human / open an incident.

---

## Agent Runtime

**Responsibility:** run one agent step — an LLM loop that reasons and calls tools
until the step goal is achieved. Full treatment in [`04-agent-design.md`](04-agent-design.md).

**Definition of an agent:**
```yaml
id: invoice_extractor
model: claude-sonnet-5           # chosen per cost/latency/capability
role: "Extract structured data from vendor invoices."
instructions: |
  You receive raw invoice text. Return vendor, invoice number, line items,
  totals, currency, and due date. If a field is missing, mark it null — never guess.
tools: [ocr_document, lookup_vendor]     # allow-list only
guardrails:
  max_steps: 8
  max_tokens: 20000
  stop_on: [structured_output_ready]
  refuse_if: [pii_export, payment_over_limit]
output_schema: { $ref: "schemas/invoice.json" }
```

Agents are **least-privilege**: they can only see the tools on their allow-list and
only the context the workflow hands them.

---

## Tool & Connector Layer

**Tool** = a typed function an agent can call. **Connector** = the authenticated
client + credentials a tool uses.

**Tool contract:**
```python
class Tool:
    name: str
    description: str          # what the model sees
    input_schema: JSONSchema  # validated before execution
    output_schema: JSONSchema
    connector: str            # which connector backs it
    side_effect: bool         # read-only vs mutating (affects policy)
    cost_hint: Cost           # for budgeting
    def run(self, args, context) -> ToolResult: ...
```

Design rules:
- **Typed I/O** validated on both sides — the model can't send malformed args, and downstream steps get a stable shape.
- **Read vs write flag** — read-only tools get lighter policy; mutating tools may require approval.
- **Idempotency** — mutating tools accept an idempotency key so retries are safe.
- **Timeouts & circuit breakers** per connector.
- **Redaction** — tool results are scrubbed of secrets before entering the model context or logs.

**Connector catalog (examples):** Gmail/Outlook, Slack/Teams, Salesforce/HubSpot,
Jira/Linear, Google Drive/SharePoint, Postgres/Snowflake, Stripe, internal REST/gRPC,
generic HTTP, and a sandboxed code-execution connector.

---

## Policy & Approval Engine

**Responsibility:** decide, for every action, whether it may proceed, must be
blocked, or requires human approval. Covered in depth in
[`05-security-governance.md`](05-security-governance.md).

Evaluated inputs: tenant, agent, tool, arguments, running cost, time of day, data
classification. Output: `ALLOW | DENY | REQUIRE_APPROVAL` + reason. Policies are
declarative and versioned.

---

## State Store & Event Log

- **State store** (Postgres): tenants, users, connectors, workflows, agents, runs,
  step state, approvals. Row-level security scopes every query to a tenant.
- **Event log** (append-only, e.g. a partitioned table or Kafka topic): the
  immutable record of everything a run did. Each event: `run_id, step_id, type,
  actor, input, output, cost, latency, policy_decision, timestamp`. This powers
  the run viewer, audit, replay, and evals.

---

## Knowledge & Memory

- **Retrieval (RAG):** company documents, past runs, and playbooks are chunked,
  embedded, and stored in a vector index; agents retrieve relevant context at run time.
- **Short-term memory:** the working context of a single run.
- **Long-term memory:** durable facts an automation learns (e.g. "vendor X always
  bills in EUR"), stored as structured records, not free text, and subject to review.

---

## Admin / Builder Console

The human surface: build workflows visually or as code, manage connectors and
secrets, set policies and budgets, review the approval queue, watch live runs,
inspect traces, and review evaluation dashboards.
