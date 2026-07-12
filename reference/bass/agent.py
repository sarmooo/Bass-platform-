"""The agent control loop.

Runs one agent step: call the model; while it requests tools, gate each call
through the policy engine, execute the allowed ones through their connector
(inheriting timeout/retry/breaker), feed results back, and repeat — until a
final answer or a stop condition (max steps).

Model interface (implemented by tools.MockModel and providers.AnthropicModel):
    respond(agent, task, scratch) -> {"type": "tool_calls", "calls":[...], "cost_usd"}
                                   | {"type": "final", "output": <any>, "cost_usd"}
"""

from __future__ import annotations

from typing import Any, Callable

from .errors import MaxStepsExceeded
from .models import Agent, PolicyEffect, Run
from .observability import Metrics, get_logger
from .policy import PolicyEngine
from .tools import ToolRegistry

log = get_logger("agent")


class AgentRuntime:
    def __init__(self, model: Any, tools: ToolRegistry, policy: PolicyEngine,
                 metrics: Metrics | None = None, emit: Callable[..., None] | None = None):
        self.model = model
        self.tools = tools
        self.policy = policy
        self.metrics = metrics or Metrics()
        # emit(run, type, payload, step_id=..., cost_usd=..., actor=...) appends a trace event
        self._emit = emit or (lambda *a, **k: None)

    def run(self, agent: Agent, task: dict, run: Run) -> dict:
        scratch: dict = {}
        for _ in range(agent.max_steps):
            resp = self.model.respond(agent, task, scratch)
            cost = float(resp.get("cost_usd", 0.0))
            run.cost_usd += cost
            self.metrics.incr("agent.model_calls", agent=agent.name)
            self._emit(run, "agent_message", {"agent": agent.name, "kind": resp["type"]},
                       cost_usd=cost, actor=agent.name)

            if resp["type"] == "tool_calls":
                scratch["tool_results"] = [
                    self._exec_call(agent, call, run) for call in resp["calls"]]
                continue

            self._emit(run, "agent_final", {"agent": agent.name, "output": resp["output"]},
                       actor=agent.name)
            return resp["output"]

        raise MaxStepsExceeded(agent.name)

    def _exec_call(self, agent: Agent, call: dict, run: Run) -> dict:
        name, args = call["tool"], call.get("args", {})
        call_id = call.get("id", name)
        tool = self.tools.get(name)

        decision = self.policy.check(agent, tool, args, run.context)
        self._emit(run, "policy_decision",
                   {"tool": name, "effect": decision.effect.value, "reason": decision.reason},
                   actor=agent.name)
        self.metrics.incr("policy.decisions", effect=decision.effect.value)

        if decision.effect != PolicyEffect.ALLOW:
            # Agent-invoked mutations aren't auto-approved mid-loop; escalate via
            # a workflow approval node instead. Deny and let the model react.
            reason = (decision.reason if decision.effect == PolicyEffect.DENY
                      else "requires approval — escalate via a workflow approval step")
            self._emit(run, "tool_denied", {"tool": name, "reason": reason}, actor=agent.name)
            return {"id": call_id, "tool": name, "error": reason}

        result = self.tools.execute(name, args)
        run.cost_usd += tool.cost_usd
        self.metrics.incr("tool.calls", tool=name)
        self._emit(run, "tool_result", {"tool": name, "result": result},
                   cost_usd=tool.cost_usd, actor=agent.name)
        return {"id": call_id, "tool": name, "result": result}
