"""The agent control loop.

Runs a single agent step: call the model, and while the model requests tools, gate
each call through the policy engine, execute allowed ones, and feed results back —
until the model returns a final answer or a stop condition fires.

Everything is appended to the run's trace, including every policy decision.
"""

from __future__ import annotations

from .models import Agent, PolicyEffect, Run
from .policy import PolicyEngine
from .tools import MockModel, ToolRegistry


class MaxStepsExceeded(Exception):
    pass


class AgentRuntime:
    def __init__(self, model: MockModel, tools: ToolRegistry, policy: PolicyEngine,
                 approval_fn=None):
        self.model = model
        self.tools = tools
        self.policy = policy
        # approval_fn(agent, tool, args, context) -> bool ; supplied by the orchestrator
        self.approval_fn = approval_fn or (lambda *a, **k: False)

    def run(self, agent: Agent, task: dict, run: Run) -> dict:
        scratch: dict = {}
        for _ in range(agent.max_steps):
            resp = self.model.respond(agent, task, scratch)
            run.log("agent_message", {"agent": agent.name, "kind": resp["type"]},
                    cost_usd=resp.get("cost_usd", 0.0))

            if resp["type"] == "tool_call":
                self._handle_tool_call(agent, resp, run)
                continue

            # final answer
            run.log("agent_final", {"agent": agent.name, "output": resp["output"]})
            return resp["output"]

        raise MaxStepsExceeded(agent.name)

    def _handle_tool_call(self, agent: Agent, resp: dict, run: Run) -> None:
        name, args = resp["tool"], resp.get("args", {})
        tool = self.tools.get(name)

        decision = self.policy.check(agent, tool, args, run.context)
        run.log("policy_decision",
                {"tool": name, "effect": decision.effect.value, "reason": decision.reason})

        if decision.effect == PolicyEffect.DENY:
            run.log("tool_denied", {"tool": name, "reason": decision.reason})
            return

        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            granted = self.approval_fn(agent, tool, args, run)
            if not granted:
                run.log("tool_denied", {"tool": name, "reason": "approval not granted"})
                return

        result = tool.fn(args)
        run.log("tool_result", {"tool": name, "result": result}, cost_usd=tool.cost_usd)
