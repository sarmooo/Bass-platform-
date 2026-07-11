# Bass — Reference Scaffold

A minimal, dependency-free Python implementation of the Bass core loop. It exists to
make the architecture concrete: you can read it in one sitting and run it without any
API keys.

It demonstrates:

- **Domain models** (`models.py`) — the entities from `docs/03-data-model.md`.
- **A policy engine** (`policy.py`) — the safety gate: allow / deny / require-approval.
- **A tool registry** (`tools.py`) — typed, side-effect-flagged tools with a mock model.
- **The agent loop** (`agent.py`) — reason → (policy check) → tool → repeat, fully traced.
- **A real model provider** (`providers.py`) — Claude via the Anthropic API, native tool use.
- **A deterministic workflow engine** (`workflow.py`) — agent/tool/branch/approval nodes.
- **An orchestrator** (`orchestrator.py`) — trigger → workflow → run.
- **Worked examples** (`example_run.py`, `example_run_live.py`) — invoice triage, end to end.

The agent loop is written against a small `respond(agent, task, scratch)` interface,
so the LLM is pluggable. Two implementations ship:

- `tools.MockModel` — deterministic and offline (the default).
- `providers.AnthropicModel` — a real Claude call that passes the agent's tool
  allow-list through the model's **native tool-use interface** and threads tool
  results back into the conversation.

Swapping one for the other is the *only* change: the loop, the policy gate, the
trace, and the workflow engine are identical for both.

## Run the offline example

```bash
cd reference
python -m bass.example_run
```

No third-party dependencies required. You should see a traced run: policy
decisions, a read-only tool call, an approval that gets granted for the $8,200
AP entry, and the final result.

## Run the live example (real Claude call)

```bash
cd reference
pip install anthropic
export ANTHROPIC_API_KEY=sk-ant-...     # or: ant auth login
python -m bass.example_run_live
```

Same workflow, but the `invoice_extractor` agent is driven by
`providers.AnthropicModel`. The extractor really parses the raw invoice, may call
`lookup_vendor` via native tool use, and returns structured JSON — then the
deterministic engine validates, branches, and posts behind the same approval
gate. This makes a real, billed API call.
