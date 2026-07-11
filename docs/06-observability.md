# 06 · Observability, Evaluation & Improvement

You cannot operate what you cannot see. Because agents are non-deterministic, Bass
treats observability and evaluation as core product surfaces, not ops add-ons.

## The trace: every run is fully observable

Each run produces an append-only stream of events (see [`03-data-model.md`](03-data-model.md)).
The **run viewer** renders this as a timeline:

- Each step: inputs, the agent's reasoning/messages, every tool call + result, the
  policy decision, latency, token usage, and cost.
- Branch decisions and why they were taken.
- Approval requests and resolutions.
- Errors, retries, and compensations.

Because the log is complete and immutable, any run can be **replayed** (re-execute
against recorded inputs) or **forked** (change a prompt/model and re-run from a point)
— time-travel debugging for AI.

## Metrics (the four questions ops always asks)

1. **Is it working?** success rate, error rate, approval rate, escalation rate per workflow.
2. **Is it fast?** step and end-to-end latency (p50/p95/p99), queue depth.
3. **What does it cost?** tokens and USD per step / run / workflow / tenant, vs budget.
4. **Is it good?** eval scores and human-feedback ratings over time.

All emitted as OpenTelemetry metrics/traces so they flow into standard dashboards
and alerting.

## Evaluation

### Offline evals (pre-deployment / CI)
Every agent and workflow ships with an **eval set**: representative inputs plus
expected outputs or graders (exact match, schema validity, an LLM-judge rubric, or a
task-specific checker). On any prompt/model/tool change, evals run in CI and block
regressions.

### Online evals (production)
- **Sampling:** a fraction of live runs are scored automatically (LLM-judge and/or
  heuristics) to detect drift.
- **Guardrail monitors:** policy-denial spikes, output-schema failures, and
  refusal-rate changes trigger alerts.
- **Shadow mode:** a new agent/model version runs alongside production without
  taking action, and its proposed outputs are compared to the live one.

### Grading approaches
| Method | Use when |
|--------|----------|
| Exact / schema match | Structured extraction, deterministic outputs. |
| Heuristic checkers | Business rules ("total = sum of line items"). |
| LLM-as-judge | Open-ended quality (tone, correctness, helpfulness). |
| Human review | High-stakes or ambiguous; also produces training/eval data. |

## Feedback loops (continuous improvement)

```
run outcome ─▶ human/auto feedback ─▶ labeled dataset ─▶ improve prompt/model/tools ─▶ eval ─▶ deploy
```

- **Outcome capture:** did the automation succeed? Was the approval accepted? Did a
  human correct the output? Each becomes a labeled example.
- **Error clustering:** group failing runs by cause to find the highest-leverage fix.
- **Prompt / workflow iteration:** changes are validated against evals before rollout.
- **Progressive rollout:** canary a new version on a small traffic slice, watch evals
  and metrics, then ramp.

## Alerting & SLOs

Define per-workflow SLOs (e.g. "95% of invoice runs complete < 2 min, success > 98%").
Alert on breach, on cost-budget burn, on rising policy denials, and on approval-queue
backlog. Alerts link straight to the offending run's trace.

## Cost management

- Real-time cost roll-ups let admins see spend per workflow/tenant live.
- Budgets (from [`03-data-model.md`](03-data-model.md)) enforce hard caps; approaching
  a cap warns, exceeding it blocks and escalates.
- Model-selection policy lets teams trade cost for capability per step.
