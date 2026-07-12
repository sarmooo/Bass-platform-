# 09 · Delivery Roadmap

A phased plan that delivers value early and hardens toward enterprise scale. Each
phase is shippable on its own.

## Phase 0 — Foundations (weeks 1–4)

**Goal:** the core loop runs one hard-coded workflow end-to-end.

- Domain model + Postgres schema (tenant, workflow, run, event).
- The deterministic workflow engine with `agent` and `tool` node types.
- Agent runtime with the LLM loop and tool-use.
- Tool registry + 2–3 connectors (HTTP, database, email).
- Append-only event log + a basic run viewer.
- **Exit criteria:** the reference invoice-triage workflow runs, is fully traced, and is replayable.

## Phase 1 — Safety & humans (weeks 5–8)

**Goal:** safe enough to act on real systems.

- Policy engine (allow/deny/require-approval) on every tool call.
- Approval node type + notifications + approval queue UI.
- Budgets and step caps (agent/workflow/tenant).
- Secrets vault integration; redaction of secrets/PII in logs.
- **Exit criteria:** a mutating workflow runs in production behind approvals and budgets.

## Phase 2 — Builder experience (weeks 9–14)

**Goal:** non-founders can build automations.

- Visual + code workflow builder in the console.
- Connector catalog with OAuth onboarding for the top 8–10 SaaS systems.
- Trigger types: webhook, email, schedule, chat.
- Versioning + promotion (dev → staging → prod).
- **Exit criteria:** an internal ops team ships an automation without engineering help.

## Phase 3 — Quality & scale (weeks 15–22)

**Goal:** trustworthy at volume.

- Offline eval framework in CI; online sampling + shadow mode.
- Observability dashboards (success/latency/cost/eval) + SLO alerting.
- Per-tenant concurrency isolation, model gateway, backpressure.
- RAG / knowledge sources with automated ingestion.
- Progressive rollout + automatic rollback on regression.
- **Exit criteria:** multiple tenants, thousands of runs/day, eval-gated deploys.

## Phase 4 — Enterprise & ecosystem (weeks 23+)

**Goal:** regulated-enterprise ready and extensible.

- SSO/SAML, fine-grained RBAC, audit export to SIEM.
- Compliance posture (SOC 2, GDPR tooling, optional HIPAA controls).
- Single-tenant / VPC / self-hosted deployment options + data residency.
- Connector SDK + marketplace so customers build their own tools.
- Multi-agent templates and a library of prebuilt automations.
- **Exit criteria:** signed enterprise customer running in a dedicated deployment.

## Guiding principles throughout

1. **Deterministic orchestration, contained non-determinism** — reliability first.
2. **Safe by default** — deny-by-default for mutations; humans for high stakes.
3. **Observable from day one** — if it isn't traced, it didn't happen.
4. **Evals gate change** — no prompt/model change ships without passing evals.
5. **Start narrow, then generalize** — nail a few high-value workflows before breadth.

## Team shape (indicative)

| Area | Focus |
|------|-------|
| Platform/backend | Engine, runtime, data, scaling. |
| Integrations | Connectors, tool SDK, auth. |
| Applied AI | Agent design, prompting, evals, model selection. |
| Product/design | Builder console, run viewer, approvals UX. |
| Security/compliance | Policy, isolation, audits, certifications. |

## Success metrics

- **Adoption:** # active workflows, # tenants, runs/day.
- **Value:** hours saved, deflection rate, cycle-time reduction per workflow.
- **Trust:** success rate, approval acceptance rate, incident count.
- **Efficiency:** cost per successful run, eval score trend.
