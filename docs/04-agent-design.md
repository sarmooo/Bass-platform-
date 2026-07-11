# 04 · Agent Design

An **agent** is one LLM-driven step. Getting the agent loop right — bounded,
observable, and safe — is what separates a demo from a production system.

## The agent control loop

```
build context ─▶ call model ─▶ model wants a tool?
     ▲                              │ yes                  │ no
     │                              ▼                      ▼
     │                       policy check ──DENY──▶ record + stop/error
     │                              │ ALLOW / (APPROVAL→pause→resume)
     │                              ▼
     └───────────── append tool result ◀── execute tool (connector)
                                    │
                          stop condition met? ─▶ yes ─▶ validate output ─▶ done
```

Pseudocode (see [`reference/bass/agent.py`](../reference/bass/agent.py) for a
runnable version):

```python
def run_agent(agent, task, context):
    messages = build_initial_messages(agent, task, context)
    for step in range(agent.max_steps):
        response = model.call(agent.model, agent.instructions, messages, agent.tools)
        log_event("agent_message", response)

        if response.tool_calls:
            for call in response.tool_calls:
                decision = policy.check(agent, call.tool, call.args, context)
                log_event("policy_decision", decision)
                if decision == DENY:
                    messages.append(tool_denied(call, decision.reason))
                    continue
                if decision == REQUIRE_APPROVAL:
                    approval = request_approval(call, context)   # pauses the run
                    if not approval.granted:
                        messages.append(tool_denied(call, "not approved"))
                        continue
                result = tools.execute(call.tool, call.args, context)  # idempotent
                log_event("tool_result", redact(result))
                messages.append(tool_result_message(call, result))
            continue  # let the model react to results

        if agent.output_schema:
            output = validate(response.text, agent.output_schema)  # retry once on failure
            return output
        return response.text
    raise MaxStepsExceeded(agent)
```

## Prompt construction

The system prompt for a step is assembled from stable pieces:

1. **Role** — one sentence: what this agent is for.
2. **Instructions** — the how: rules, format, edge cases, what *not* to do.
3. **Tool descriptions** — supplied via the model's native tool-use interface, not pasted into text.
4. **Retrieved context** — RAG results relevant to the task, clearly delimited and labeled as *reference data, not instructions*.
5. **Task input** — the concrete thing to act on, from the workflow.

Untrusted content (email bodies, ticket text, web pages) is **always** wrapped and
labeled as data, never merged into the instruction region — the primary defense
against prompt injection.

## Model selection per step

Not every step needs the biggest model. Bass picks per step:

| Step character | Suggested model |
|----------------|-----------------|
| High-stakes reasoning, ambiguous judgment, planning | Claude Opus |
| Everyday extraction, drafting, routing, tool use | Claude Sonnet |
| High-volume, simple classification / formatting | Claude Haiku |

The workflow author sets a default; the runtime can downgrade/upgrade based on
budget and observed difficulty.

## Guardrails (defense in depth)

- **Structural:** tool allow-list, typed arguments, output schema validation.
- **Budgetary:** max steps, max tokens, max cost per step and per run.
- **Behavioral:** `refuse_if` conditions; a lightweight classifier can screen
  inputs/outputs for policy violations (PII exfiltration, unsafe actions).
- **Human:** approval nodes for irreversible or high-value actions.
- **Injection defense:** untrusted-data framing, and tools never execute
  instructions found *inside* retrieved data.

## Memory

- **Working memory** — the message list for the current step/run.
- **Retrieval** — pull relevant documents/past-run summaries at run time (RAG).
- **Long-term learned facts** — promoted deliberately (e.g. an approver confirms
  "vendor X bills in EUR"), stored as structured, reviewable records — not silent,
  unbounded free-text accumulation.

## Multi-agent patterns

Composed via the **workflow engine**, not by agents spawning agents ad hoc (which
is hard to bound and observe):

- **Pipeline** — extractor → validator → poster, each a step.
- **Router** — a classifier agent picks which branch/sub-workflow to run.
- **Planner/executor** — a planner produces a checklist; executor steps carry it out.
- **Reviewer** — a second agent critiques the first agent's output before a
  high-stakes action (adversarial check).

Keeping composition in the deterministic engine means every hand-off is logged,
retryable, and budget-bounded.

## Evaluating agents

Every agent should ship with an **eval set**: representative inputs + expected
outcomes or graders. Evals run in CI on prompt/model changes and continuously
against sampled production traffic. See [`06-observability.md`](06-observability.md).
