"""Real model provider — Claude via the Anthropic API.

`AnthropicModel` implements the same `respond(agent, task, scratch)` interface as
`tools.MockModel`, so dropping it into the orchestrator turns the offline demo
into a live one with no other changes (docs/04-agent-design.md).

Design points that matter here:
- The agent's tool allow-list is passed through the model's **native tool-use
  interface** (`tools=`), not pasted into the prompt.
- Untrusted task content is framed and labelled as *data*, never merged into the
  instruction region — the primary defense against prompt injection.
- Conversation state for one agent step lives in `scratch`, so the deterministic
  workflow engine and policy gate around it are unchanged.

Requires `pip install anthropic` and an `ANTHROPIC_API_KEY` (or an `ant auth
login` profile). It is intentionally *not* imported by the default example.
"""

from __future__ import annotations

import json
from typing import Any

from .models import Agent
from .tools import ToolRegistry

# Per-1M-token prices (USD) for cost roll-ups; extend as needed.
# Source: Anthropic pricing at time of writing.
_PRICES = {
    "claude-opus-4-8":  {"in": 5.0,  "out": 25.0},
    "claude-sonnet-5":  {"in": 3.0,  "out": 15.0},
    "claude-haiku-4-5": {"in": 1.0,  "out": 5.0},
}
_DEFAULT_MODEL = "claude-opus-4-8"


class AnthropicModel:
    """Drives one agent step against the Claude API, honoring the agent's
    model, instructions, tool allow-list, and output schema."""

    def __init__(self, tools: ToolRegistry, client: Any = None, max_tokens: int = 4096):
        # Import lazily so the package works offline without the SDK installed.
        if client is None:
            import anthropic  # noqa: PLC0415
            client = anthropic.Anthropic()
        self._client = client
        self._tools = tools
        self._max_tokens = max_tokens

    # -- the model interface the agent loop calls --------------------------

    def respond(self, agent: Agent, task: dict, scratch: dict) -> dict:
        messages = scratch.get("messages")
        if messages is None:
            messages = [{"role": "user", "content": self._task_block(task)}]
            scratch["messages"] = messages

        # Feed back any tool results the runtime produced on the previous turn.
        pending = scratch.pop("tool_results", None)
        if pending:
            messages.append({"role": "user", "content": self._result_blocks(pending)})

        resp = self._client.messages.create(
            model=agent.model or _DEFAULT_MODEL,
            max_tokens=self._max_tokens,
            system=self._system_prompt(agent),
            tools=self._tool_schemas(agent),
            thinking={"type": "adaptive"},   # let Claude decide when to reason
            messages=messages,
        )

        # Round-trip the assistant turn verbatim (preserves tool_use blocks).
        messages.append({"role": "assistant", "content": resp.content})
        cost = self._cost(resp, agent.model or _DEFAULT_MODEL)

        if resp.stop_reason == "tool_use":
            calls = [
                {"id": b.id, "tool": b.name, "args": b.input}
                for b in resp.content if b.type == "tool_use"
            ]
            return {"type": "tool_calls", "calls": calls, "cost_usd": cost}

        text = "".join(b.text for b in resp.content if b.type == "text")
        return {"type": "final", "output": self._parse_output(agent, text), "cost_usd": cost}

    # -- prompt / schema assembly ------------------------------------------

    @staticmethod
    def _system_prompt(agent: Agent) -> str:
        parts = [agent.role, agent.instructions]
        if agent.output_schema is not None:
            parts.append(
                "Return ONLY a single JSON object matching this schema, with no "
                "prose or code fences:\n" + json.dumps(agent.output_schema)
            )
        return "\n\n".join(p for p in parts if p)

    @staticmethod
    def _task_block(task: dict) -> list[dict]:
        # Untrusted input is labelled as data, not instructions (injection defense).
        return [{
            "type": "text",
            "text": "<task_input>\n" + json.dumps(task, indent=2) + "\n</task_input>",
        }]

    def _tool_schemas(self, agent: Agent) -> list[dict]:
        schemas = []
        for name in agent.tools:            # allow-list only
            t = self._tools.get(name)
            schemas.append({
                "name": t.name,
                "description": t.description,
                "input_schema": t.input_schema,
            })
        return schemas

    @staticmethod
    def _result_blocks(results: list[dict]) -> list[dict]:
        blocks = []
        for r in results:
            block = {"type": "tool_result", "tool_use_id": r["id"]}
            if "error" in r:
                block["content"] = str(r["error"])
                block["is_error"] = True
            else:
                block["content"] = json.dumps(r["result"])
            blocks.append(block)
        return blocks

    @staticmethod
    def _parse_output(agent: Agent, text: str) -> Any:
        if agent.output_schema is None:
            return text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Best-effort: pull the first {...} span if the model wrapped it.
            start, end = text.find("{"), text.rfind("}")
            if start != -1 and end > start:
                return json.loads(text[start:end + 1])
            raise

    @staticmethod
    def _cost(resp: Any, model: str) -> float:
        price = _PRICES.get(model, _PRICES[_DEFAULT_MODEL])
        u = resp.usage
        return (u.input_tokens * price["in"] + u.output_tokens * price["out"]) / 1_000_000
