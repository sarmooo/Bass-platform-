# 05 · Security & Governance

AI systems that can *act* on company systems are only adoptable if they are safe by
construction. Governance in Bass is a core layer, not an afterthought.

## Threat model (what we defend against)

| Threat | Defense |
|--------|---------|
| Prompt injection via untrusted content | Untrusted data is framed/labeled as data; tools never obey instructions embedded in retrieved content; output validated. |
| Agent taking a harmful/irreversible action | Policy gate on every tool call; approval nodes; read-only vs write distinction; per-tool limits. |
| Data exfiltration | Least-privilege tool allow-lists; egress policy; redaction; DLP classifier on outbound content. |
| Cross-tenant data leakage | Hard isolation: RLS, per-tenant vault namespaces, scoped connectors. |
| Credential theft | Secrets never enter model context or logs; short-lived tokens; vault-managed rotation. |
| Runaway cost / infinite loops | Budgets and step caps at agent/workflow/tenant scope; circuit breakers. |
| Model producing wrong output | Output schemas, verifier agents, evals, human approval for high-stakes. |

## Identity & access

- **Humans:** SSO via OIDC/SAML; RBAC roles (owner, admin, builder, approver, viewer).
- **Machines:** scoped API keys / OAuth clients per integration.
- **Agents:** each agent is a least-privilege principal with an explicit tool allow-list — it is *not* granted the tenant's full access.

## Tenant isolation

- Postgres **row-level security** scopes every query by `tenant_id`.
- Each tenant has an isolated **vault namespace** for connector secrets.
- Connectors are tenant-scoped; a tool call can only use its tenant's connectors.
- Optional dedicated compute / data residency region per tenant (`settings.data_residency`).

## The policy engine

The single decision point for "may this happen?" Evaluated on every tool call and
sensitive workflow transition.

**Inputs:** `tenant, agent, tool, arguments, running_cost, data_classification,
time, actor_role`.
**Output:** `ALLOW | DENY | REQUIRE_APPROVAL` + human-readable reason (logged).

Policies are **declarative, versioned, and ordered by priority**:

```yaml
policies:
  - name: block-external-email-with-pii
    scope: { tool: send_email }
    when: "args.to is external AND args.body contains pii"
    effect: DENY

  - name: large-payments-need-approval
    scope: { tool: create_payment }
    when: "args.amount_usd > 5000"
    effect: REQUIRE_APPROVAL
    approvers: ["finance-team"]

  - name: read-only-tools-always-ok
    scope: { side_effect: false }
    effect: ALLOW
```

Default posture is **deny-by-default for mutating tools**: an action must be
explicitly allowed (or approved) to run.

## Approvals & human-in-the-loop

Approval is a **workflow node type**, so it's durable and auditable:
- The run pauses; approvers are notified (Slack/email/console) with full context.
- Approvers see the proposed action, arguments, agent reasoning, and cost.
- Timeouts route to a fallback (escalate, auto-reject, notify).
- Every decision (who, when, why) is recorded in the event log.

## Data protection

- **Encryption** in transit (TLS) and at rest (per-tenant keys / envelope encryption).
- **Redaction** of secrets and configurable PII before content enters model context or logs.
- **Retention** policies per tenant; event payloads purgeable while keeping cost/outcome summaries.
- **DLP** classifier screens outbound content for sensitive data before egress tools run.

## Auditability & compliance

- The append-only event log is a complete, tamper-evident audit trail: every action,
  who/what initiated it, the policy decision, inputs, outputs, and cost.
- Exportable audit reports per tenant / per run / per user.
- Designed to support **SOC 2**, **GDPR** (data subject access/erasure via retention
  + redaction tooling), and **HIPAA**-style controls where the plan requires it.
- Model I/O can be excluded from provider training and logged only within the tenant boundary.

## Safe failure

When in doubt, Bass fails **closed**: an unrecognized tool, an over-budget run, or an
unavailable policy engine results in the action being blocked and escalated, not
silently allowed.
