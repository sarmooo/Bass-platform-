"""The agent control loop.

Runs a single agent step: call the model, and while the model requests tools,
gate each call through the policy engine, execute the allowed ones, feed the
results back, and repeat — until the model returns a final answer or a stop
condition fires.

Everything is appended to the run's trace, including every policy decision.

The loop is written against a small model interface so the LLM is pluggable:

    model.respond(agent, task, scratch) -> dict
        {"type": "tool_calls", "calls": [{"id", "tool", "args"}], "cost_usd": x}
      | {"type": "final",      "output": <any>,                  "cost_usd": x}

    `scratch` is a per-run dict the model may use to carry conversation state
    across iterations; the runtime writes executed tool results into
    scratch["tool_results"] for the model to feed back on the next turn.

`tools.MockModel` implements this offline; `providers.AnthropicModel` implements
it against the real Claude API. The loop below is identical for both.
"""

from __future__ import annotations

from .models import Agent, PolicyEffect, Run
from .policy import PolicyEngine
from .tools import ToolRegistry


class MaxStepsExceeded(Exception):
    pass


class AgentRuntime:
    def __init__(self, model, tools: ToolRegistry, policy: PolicyEngine,
                 approval_fn=None):
        self.model = model
        self.tools = tools
        self.policy = policy
        # approval_fn(agent, tool, args, run) -> bool ; supplied by the orchestrator
        self.approval_fn = approval_fn or (lambda *a, **k: False)

    def run(self, agent: Agent, task: dict, run: Run) -> dict:
        scratch: dict = {}
        for _ in range(agent.max_steps):
            resp = self.model.respond(agent, task, scratch)
            run.log("agent_message",
                    {"agent": agent.name, "kind": resp["type"]},
                    cost_usd=resp.get("cost_usd", 0.0))

            if resp["type"] == "tool_calls":
                # Execute every requested call, then hand results back next turn.
                scratch["tool_results"] = [
                    self._exec_call(agent, call, run) for call in resp["calls"]
                ]
                continue

            # final answer
            run.log("agent_final", {"agent": agent.name, "output": resp["output"]})
            return resp["output"]

        raise MaxStepsExceeded(agent.name)

    def _exec_call(self, agent: Agent, call: dict, run: Run) -> dict:
        """Policy-gate and execute one tool call; return a result record."""
        name, args = call["tool"], call.get("args", {})
        call_id = call.get("id", name)
        tool = self.tools.get(name)

        decision = self.policy.check(agent, tool, args, run.context)
        run.log("policy_decision",
                {"tool": name, "effect": decision.effect.value, "reason": decision.reason})

        if decision.effect == PolicyEffect.DENY:
            run.log("tool_denied", {"tool": name, "reason": decision.reason})
            return {"id": call_id, "tool": name, "error": decision.reason}

        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            if not self.approval_fn(agent, tool, args, run):
                run.log("tool_denied", {"tool": name, "reason": "approval not granted"})
                return {"id": call_id, "tool": name, "error": "approval not granted"}

        result = tool.fn(args)
        run.log("tool_result", {"tool": name, "result": result}, cost_usd=tool.cost_usd)
        return {"id": call_id, "tool": name, "result": result}
