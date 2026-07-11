# 03 · Data Model

The data model is deliberately small and orthogonal. Everything a company builds is
a composition of these entities. All tables carry a `tenant_id` and are protected by
row-level security.

## Entity relationship overview

```
Tenant 1───* User
Tenant 1───* Connector 1───* Tool
Tenant 1───* Agent  *───* Tool        (allow-list join)
Tenant 1───* Workflow 1───* Step
Workflow 1───* Trigger
Workflow 1───* Run 1───* StepExecution 1───* Event
Run 1───* Approval
Tenant 1───* Policy
Tenant 1───* Budget
Tenant 1───* KnowledgeSource 1───* Document 1───* Chunk(embedding)
```

## Core tables

### tenant
| column | type | notes |
|--------|------|-------|
| id | uuid | PK |
| name | text | |
| plan | text | billing tier |
| created_at | timestamptz | |
| settings | jsonb | data residency, default model, etc. |

### user
`id, tenant_id, email, role (owner/admin/builder/approver/viewer), sso_subject, created_at`

### connector
`id, tenant_id, type (gmail/slack/postgres/...), name, config jsonb, secret_ref (vault path), status, created_at`
Credentials live in the vault; only `secret_ref` is stored here.

### tool
`id, tenant_id, connector_id, name, description, input_schema jsonb, output_schema jsonb, side_effect bool, cost_hint jsonb, enabled bool`

### agent
`id, tenant_id, name, model, role, instructions, guardrails jsonb, output_schema jsonb, version`
Join table `agent_tool (agent_id, tool_id)` implements the allow-list.

### workflow
`id, tenant_id, name, version, definition jsonb, status (draft/active/archived), budget jsonb, created_by, created_at`
`definition` holds the declarative step graph. Versioned — old runs pin the version they ran under.

### trigger
`id, tenant_id, workflow_id, type (event/schedule/manual), match jsonb, schedule text, enabled bool`

### run
| column | type | notes |
|--------|------|-------|
| id | uuid | PK |
| tenant_id | uuid | |
| workflow_id | uuid | |
| workflow_version | int | pinned |
| trigger_event | jsonb | what started it |
| status | enum | pending/running/waiting_approval/succeeded/failed/canceled |
| idempotency_key | text | dedup |
| cost_usd | numeric | rolled up |
| started_at / ended_at | timestamptz | |
| result | jsonb | final output |

### step_execution
`id, run_id, step_id, node_type, status, attempt, input jsonb, output jsonb, cost_usd, started_at, ended_at, error jsonb`

### event  (append-only)
| column | type | notes |
|--------|------|-------|
| id | bigserial | PK, monotonic |
| run_id | uuid | |
| step_id | text | nullable |
| type | text | `agent_message`, `tool_call`, `tool_result`, `policy_decision`, `approval_requested`, `approval_resolved`, `error`, `state_transition` |
| actor | text | agent / system / user id |
| payload | jsonb | inputs/outputs (redacted) |
| cost_usd | numeric | |
| latency_ms | int | |
| created_at | timestamptz | |

This is the **trace**. Never updated, only appended — the immutable behavioral record.

### approval
`id, run_id, step_id, approvers jsonb, status (pending/approved/rejected/expired), decided_by, decision_note, requested_at, decided_at, timeout_at`

### policy
`id, tenant_id, name, scope jsonb (which agents/tools), rule jsonb, effect (allow/deny/require_approval), priority, enabled, version`

### budget
`id, tenant_id, scope (tenant/workflow/agent), period (run/day/month), max_usd, max_steps, current_usd, resets_at`

### knowledge_source / document / chunk
`knowledge_source(id, tenant_id, type, config)`
`document(id, source_id, uri, title, hash, updated_at)`
`chunk(id, document_id, ordinal, text, embedding vector, metadata jsonb)`

## Design notes

- **Versioning everywhere that matters.** Workflows and agents are versioned; a run
  pins the versions it executed under so historical runs remain reproducible even
  after definitions change.
- **Redaction at write time.** Sensitive fields are redacted before an event is
  persisted, not at read time — the raw secret never lands in the log.
- **Cost is a first-class column,** rolled up from events → step → run → tenant, so
  budgets and billing are exact, not estimated.
- **Soft-delete + retention.** Data has per-tenant retention policies (e.g. purge
  event payloads after 90 days but keep the cost/outcome summary indefinitely).
