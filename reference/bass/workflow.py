"""The deterministic workflow engine.

Walks a workflow's step graph, executing one node at a time and committing shared
state to the run's context between steps. Node types: agent, tool, branch, approval.

The key design point: orchestration is deterministic and fully traced. The only
non-determinism (the model) is confined to `agent` nodes. This is what makes the
system debuggable and replayable (docs/01-architecture.md §4).
"""

from __future__ import annotations

from .agent import AgentRuntime
from .models import Agent, PolicyEffect, Run, RunStatus, Step, Workflow
from .policy import PolicyEngine
from .tools import ToolRegistry


class WorkflowEngine:
    def __init__(self, agents: dict[str, Agent], tools: ToolRegistry,
                 policy: PolicyEngine, runtime: AgentRuntime):
        self.agents = agents
        self.tools = tools
        self.policy = policy
        self.runtime = runtime

    def execute(self, wf: Workflow, run: Run) -> Run:
        run.status = RunStatus.RUNNING
        step_id: str | None = wf.start

        while step_id is not None:
            step = wf.steps[step_id]
            run.log("state_transition", {"entering_step": step.id, "type": step.node_type},
                    step_id=step.id)

            # Budget guard — fail closed if a run blows its cost cap.
            if run.cost_usd > wf.budget_usd:
                run.status = RunStatus.FAILED
                run.log("error", {"reason": "budget exceeded", "cost_usd": run.cost_usd})
                return run

            step_id = self._run_step(step, run)

        run.status = RunStatus.SUCCEEDED
        run.result = run.context.get("result", run.context)
        run.log("run_succeeded", {"result": run.result})
        return run

    def _run_step(self, step: Step, run: Run) -> str | None:
        if step.node_type == "agent":
            return self._run_agent_step(step, run)
        if step.node_type == "tool":
            return self._run_tool_step(step, run)
        if step.node_type == "branch":
            return self._run_branch_step(step, run)
        if step.node_type == "approval":
            # Handled inline by the agent runtime's approval hook in this scaffold;
            # as a standalone node it simply records intent and continues.
            run.log("approval_node", {"approvers": step.approvers}, step_id=step.id)
            return step.next
        raise ValueError(f"unknown node type: {step.node_type}")

    def _run_agent_step(self, step: Step, run: Run) -> str | None:
        agent = self.agents[step.agent]
        task = self._resolve_args(step.args, run) or {"email": run.trigger_event}
        output = self.runtime.run(agent, task, run)
        # Publish the agent's output into shared context under the step id.
        run.context[step.id] = output
        return step.next

    def _run_tool_step(self, step: Step, run: Run) -> str | None:
        tool = self.tools.get(step.tool)
        args = self._resolve_args(step.args, run)
        # Tool steps are subject to the same policy gate as agent-invoked tools.
        # We evaluate against a synthetic "system" agent that is allowed this tool.
        system = Agent(name="system", role="workflow", instructions="", tools=[step.tool])
        decision = self.policy.check(system, tool, args, run.context)
        run.log("policy_decision",
                {"tool": step.tool, "effect": decision.effect.value, "reason": decision.reason},
                step_id=step.id)

        if decision.effect == PolicyEffect.DENY:
            run.log("tool_denied", {"tool": step.tool, "reason": decision.reason}, step_id=step.id)
            run.context[step.id] = {"denied": True, "reason": decision.reason}
            return step.next

        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            granted = self.runtime.approval_fn(system, tool, args, run)
            if not granted:
                run.log("tool_denied", {"tool": step.tool, "reason": "approval not granted"},
                        step_id=step.id)
                run.context[step.id] = {"denied": True, "reason": "approval not granted"}
                return step.next

        result = tool.fn(args)
        run.log("tool_result", {"tool": step.tool, "result": result},
                step_id=step.id, cost_usd=tool.cost_usd)
        run.context[step.id] = result
        return step.next

    def _run_branch_step(self, step: Step, run: Run) -> str | None:
        for predicate, goto in step.branches:
            if predicate is None or predicate(run.context):
                run.log("branch_taken", {"goto": goto}, step_id=step.id)
                return goto
        return step.next

    @staticmethod
    def _resolve_args(args: dict, run: Run) -> dict:
        """Resolve callables in args against the run context (tiny template stand-in)."""
        resolved = {}
        for k, v in args.items():
            resolved[k] = v(run.context) if callable(v) else v
        return resolved
