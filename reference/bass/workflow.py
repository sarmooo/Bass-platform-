"""The durable workflow engine.

Walks a workflow's step graph one node at a time, committing progress to the
store after every step. The design point (docs/01-architecture.md §4): keep
orchestration deterministic and durable; confine non-determinism (the model) to
agent nodes.

Production properties implemented here:
- **Crash-safe.** After each step the run's cursor, context, cost, and new events
  are checkpointed in one transaction. A process that dies mid-run resumes from
  the last committed cursor — just re-hydrate the run and call `resume`.
- **Exactly-once side effects.** Mutating tool steps consult an idempotency
  ledger before acting and record their result after; a replay returns the
  recorded result instead of acting twice.
- **Budgeted.** Cost and step ceilings are enforced before each step; a breach
  fails the run closed.
- **Reliable.** Steps retry up to `max_attempts`; a step may name a compensating
  step to run on terminal failure (saga pattern).
- **Governed.** Mutating tool steps pass the policy gate and can require a durable
  human approval before executing.
"""

from __future__ import annotations

from typing import Callable, Optional

from .agent import AgentRuntime
from .budgets import Budget
from .connectors import ConnectorError
from .errors import BudgetExceeded, PolicyDenied, WorkflowError
from .models import (Agent, Event, PolicyEffect, Run, RunStatus, Step, Workflow)
from .observability import Metrics, get_logger, set_trace_id
from .policy import PolicyEngine
from .store import Store
from .tools import ToolRegistry

log = get_logger("engine")

# approval_fn(run, step, tool, args) -> bool. Optional: if provided, approvals
# resolve synchronously (used by the CLI examples and tests). If None, approvals
# are asynchronous — the run pauses (WAITING_APPROVAL) and an external caller
# resolves the approval, then resumes the run. That async path is what the HTTP
# API uses (bass/api.py).
ApprovalFn = Callable[[Run, Step, str, dict], bool]


class _Paused(Exception):
    """Internal signal: the run paused awaiting an external approval decision."""

    def __init__(self, approval_id: str):
        self.approval_id = approval_id


class WorkflowEngine:
    def __init__(self, store: Store, agents: dict[str, Agent], tools: ToolRegistry,
                 policy: PolicyEngine, runtime: AgentRuntime,
                 approval_fn: ApprovalFn | None = None, metrics: Metrics | None = None):
        self.store = store
        self.agents = agents
        self.tools = tools
        self.policy = policy
        self.runtime = runtime
        self.approval_fn = approval_fn          # None => asynchronous approvals
        self.metrics = metrics or Metrics()
        self._buffer: list[Event] = []           # events for the current step

    # -- trace emission (buffered, flushed atomically at checkpoint) --------

    def emit(self, run: Run, type: str, payload: dict, step_id: Optional[str] = None,
             cost_usd: float = 0.0, actor: str = "system") -> None:
        self._buffer.append(Event(run_id=run.id, tenant_id=run.tenant_id, type=type,
                                  payload=payload, step_id=step_id, actor=actor,
                                  cost_usd=cost_usd))

    def _checkpoint(self, run: Run) -> None:
        self.store.checkpoint(run, self._buffer)
        self._buffer = []

    # -- execution ----------------------------------------------------------

    def resume(self, run: Run, wf: Workflow) -> Run:
        """Run to completion (or pause) from the run's current cursor."""
        set_trace_id(run.id)
        budget = Budget(max_usd=wf.budget_usd, max_steps=wf.max_steps)

        if run.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELED):
            return run
        run.status = RunStatus.RUNNING
        step_id = run.cursor_step if run.cursor_step is not None else wf.start

        try:
            self._drive(run, wf, budget, step_id)   # returns None at completion
        except _Paused:
            self.metrics.incr("runs.paused")
            return run                           # WAITING_APPROVAL already persisted

        self.metrics.incr("runs.succeeded")
        return run

    def _drive(self, run: Run, wf: Workflow, budget: Budget,
               step_id: Optional[str]) -> Optional[str]:
        while step_id is not None:
            if step_id not in wf.steps:
                raise WorkflowError(f"unknown step '{step_id}'")
            step = wf.steps[step_id]

            violation = budget.check(run)
            if violation:
                run.status = RunStatus.FAILED
                self.emit(run, "error", {"reason": "budget exceeded", "detail": violation})
                self._checkpoint(run)
                self.metrics.incr("runs.failed", reason="budget")
                raise BudgetExceeded(violation)

            run.steps_used += 1
            self.emit(run, "state_transition",
                      {"entering_step": step.id, "type": step.node_type}, step_id=step.id)

            try:
                next_id = self._run_step_with_retry(step, run, wf)
            except PolicyDenied as exc:
                run.status = RunStatus.FAILED
                self.emit(run, "error", {"reason": "policy denied", "detail": str(exc)})
                self._checkpoint(run)
                self.metrics.incr("runs.failed", reason="policy")
                raise
            except ConnectorError as exc:
                self._compensate(step, run, wf)
                run.status = RunStatus.FAILED
                self.emit(run, "error", {"reason": "step failed", "detail": str(exc)})
                self._checkpoint(run)
                self.metrics.incr("runs.failed", reason="connector")
                raise

            run.cursor_step = next_id
            if next_id is None:
                run.status = RunStatus.SUCCEEDED
                run.result = run.context.get("result", run.context)
                self.emit(run, "run_succeeded", {"result": run.result})
            self._checkpoint(run)               # durable barrier between steps
            step_id = next_id

        return step_id

    def _run_step_with_retry(self, step: Step, run: Run, wf: Workflow) -> Optional[str]:
        last: Exception | None = None
        for attempt in range(1, step.max_attempts + 1):
            try:
                return self._run_step(step, run, wf)
            except ConnectorError as exc:
                last = exc
                self.emit(run, "step_retry",
                          {"step": step.id, "attempt": attempt, "error": str(exc)},
                          step_id=step.id)
                self.metrics.incr("steps.retries", step=step.id)
        assert last is not None
        raise last

    def _run_step(self, step: Step, run: Run, wf: Workflow) -> Optional[str]:
        if step.node_type == "agent":
            return self._run_agent(step, run)
        if step.node_type == "tool":
            return self._run_tool(step, run)
        if step.node_type == "branch":
            return self._run_branch(step, run)
        if step.node_type == "approval":
            return self._run_approval(step, run)
        if step.node_type == "terminate":
            return None
        raise WorkflowError(f"unknown node type: {step.node_type}")

    # -- node handlers ------------------------------------------------------

    def _run_agent(self, step: Step, run: Run) -> Optional[str]:
        if step.agent is None or step.agent not in self.agents:
            raise WorkflowError(f"unknown agent '{step.agent}'")
        agent = self.agents[step.agent]
        task = self._resolve(step.args, run) or {"email": run.trigger_event}
        output = self.runtime.run(agent, task, run)
        run.context[step.id] = output
        return step.next

    def _run_tool(self, step: Step, run: Run) -> Optional[str]:
        assert step.tool is not None, "tool node requires a tool name"
        tool = self.tools.get(step.tool)
        args = self._resolve(step.args, run)
        key = f"{run.id}:{step.id}:{step.tool}"

        # Exactly-once: if this side effect already happened (e.g. we crashed
        # after acting but before checkpointing), replay the recorded result and
        # skip policy/approval/execution entirely — the decision was already made.
        if tool.side_effect:
            cached = self.store.get_effect(run.tenant_id, key)
            if cached is not None:
                self.emit(run, "tool_result_replayed",
                          {"tool": step.tool, "result": cached}, step_id=step.id)
                run.context[step.id] = cached
                return step.next

        # Same policy gate as an agent-invoked call, against a system principal.
        system = Agent(name="system", role="workflow", instructions="", tools=[step.tool])
        decision = self.policy.check(system, tool, args, run.context)
        self.emit(run, "policy_decision",
                  {"tool": step.tool, "effect": decision.effect.value, "reason": decision.reason},
                  step_id=step.id)
        self.metrics.incr("policy.decisions", effect=decision.effect.value)

        if decision.effect == PolicyEffect.DENY:
            raise PolicyDenied(f"{step.tool}: {decision.reason}")

        if decision.effect == PolicyEffect.REQUIRE_APPROVAL:
            if not self._await_approval(step, run, decision.approvers, tool_name=step.tool,
                                        args=args):
                raise PolicyDenied(f"{step.tool}: approval not granted")

        if tool.side_effect:
            args = {**args, "_idempotency_key": key}   # connector also dedupes on this
        result = self.tools.execute(step.tool, args)
        if tool.side_effect:
            self.store.record_effect(run.tenant_id, key, run.id, step.id, step.tool, result)
        run.cost_usd += tool.cost_usd
        self.emit(run, "tool_result", {"tool": step.tool, "result": result},
                  step_id=step.id, cost_usd=tool.cost_usd)
        self.metrics.incr("tool.calls", tool=step.tool)
        run.context[step.id] = result
        return step.next

    def _run_branch(self, step: Step, run: Run) -> Optional[str]:
        for predicate, goto in step.branches:
            if predicate is None or predicate(run.context):
                self.emit(run, "branch_taken", {"goto": goto}, step_id=step.id)
                return goto
        return step.next

    def _run_approval(self, step: Step, run: Run) -> Optional[str]:
        if not self._await_approval(step, run, step.approvers):
            raise PolicyDenied(f"approval step '{step.id}' rejected")
        return step.next

    # -- approvals (synchronous or asynchronous) ---------------------------

    def _await_approval(self, step: Step, run: Run, approvers: list[str],
                        tool_name: str = "", args: dict | None = None) -> bool:
        """Return True to proceed, False if rejected. Raises `_Paused` when the
        decision is asynchronous and not yet made."""
        if self.approval_fn is not None:
            # Synchronous: resolve inline (CLI examples, tests).
            approval_id = self.store.create_approval(run.tenant_id, run.id, step.id, approvers)
            run.status = RunStatus.WAITING_APPROVAL
            self.emit(run, "approval_requested",
                      {"approval_id": approval_id, "approvers": approvers,
                       "tool": tool_name, "amount": (args or {}).get("amount")},
                      step_id=step.id)
            granted = self.approval_fn(run, step, tool_name, args or {})
            self.store.resolve_approval(run.tenant_id, approval_id, granted,
                                        decided_by="approval_fn")
            self.emit(run, "approval_resolved",
                      {"approval_id": approval_id, "granted": granted}, step_id=step.id)
            run.status = RunStatus.RUNNING
            return granted

        # Asynchronous: an external caller (the HTTP API) resolves the approval.
        existing = self.store.latest_approval(run.tenant_id, run.id, step.id)
        if existing is None:
            approval_id = self.store.create_approval(run.tenant_id, run.id, step.id, approvers)
            run.status = RunStatus.WAITING_APPROVAL
            self.emit(run, "approval_requested",
                      {"approval_id": approval_id, "approvers": approvers,
                       "tool": tool_name, "amount": (args or {}).get("amount")},
                      step_id=step.id)
            self._checkpoint(run)                # persist the paused state + request
            raise _Paused(approval_id)
        approval_id, status = existing
        if status == "pending":
            run.status = RunStatus.WAITING_APPROVAL
            self._checkpoint(run)
            raise _Paused(approval_id)
        self.emit(run, "approval_resolved",
                  {"approval_id": approval_id, "granted": status == "approved"},
                  step_id=step.id)
        return status == "approved"

    def _compensate(self, step: Step, run: Run, wf: Workflow) -> None:
        if not step.compensate_with:
            return
        comp = wf.steps.get(step.compensate_with)
        if comp is None:
            return
        self.emit(run, "compensation", {"for_step": step.id, "run_step": comp.id},
                  step_id=comp.id)
        try:
            self._run_step(comp, run, wf)
        except Exception as exc:                 # compensation is best-effort
            self.emit(run, "compensation_failed", {"step": comp.id, "error": str(exc)},
                      step_id=comp.id)

    @staticmethod
    def _resolve(args: dict, run: Run) -> dict:
        return {k: (v(run.context) if callable(v) else v) for k, v in args.items()}
