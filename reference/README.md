# Bass — Reference Scaffold

A minimal, dependency-free Python implementation of the Bass core loop. It exists to
make the architecture concrete: you can read it in one sitting and run it without any
API keys.

It demonstrates:

- **Domain models** (`models.py`) — the entities from `docs/03-data-model.md`.
- **A policy engine** (`policy.py`) — the safety gate: allow / deny / require-approval.
- **A tool registry** (`tools.py`) — typed, side-effect-flagged tools with a mock model.
- **The agent loop** (`agent.py`) — reason → (policy check) → tool → repeat, fully traced.
- **A deterministic workflow engine** (`workflow.py`) — agent/tool/branch/approval nodes.
- **An orchestrator** (`orchestrator.py`) — trigger → workflow → run.
- **A worked example** (`example_run.py`) — invoice triage, end to end.

The LLM is **mocked** (`tools.MockModel`) so the example is deterministic and offline.
In a real deployment you'd swap `MockModel` for a call to the Anthropic API and pass
the agent's tool allow-list through the model's native tool-use interface. Everything
else — the loop, the policy gate, the trace, the workflow engine — stays the same.

## Run it

```bash
cd reference
python -m bass.example_run
```

No third-party dependencies are required (`requirements.txt` is empty by design). You
should see a traced run: policy decisions, a tool call, an approval that gets granted,
and the final AP entry.
