"""The policy engine — the safety gate.

Every tool call is evaluated here before it runs. Policies are declarative and
versioned; they are ordered by priority and the first match wins. The engine is
**fail-closed**: unknown tools, tools outside an agent's allow-list, and mutating
tools with no permitting policy are all denied by default.

A production deployment would load rules from the store (per tenant, versioned)
rather than from code; the evaluation contract below is unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .models import Agent, PolicyEffect, Tool


@dataclass
class Decision:
    effect: PolicyEffect
    reason: str
    approvers: list[str] = field(default_factory=list)


@dataclass
class Policy:
    name: str
    version: int
    # applies(agent, tool, args, context) -> bool
    applies: Callable[[Agent, Tool, dict, dict], bool]
    effect: PolicyEffect
    reason: str
    approvers: list[str] = field(default_factory=list)
    priority: int = 100                          # lower = evaluated first


class PolicyEngine:
    def __init__(self, policies: list[Policy]):
        self.policies = sorted(policies, key=lambda p: p.priority)

    def check(self, agent: Agent, tool: Tool, args: dict, context: dict) -> Decision:
        # 1. Least privilege: an agent may only call tools on its allow-list.
        if tool.name not in agent.tools:
            return Decision(PolicyEffect.DENY,
                            f"'{tool.name}' not in {agent.name}'s allow-list")
        # 2. First matching explicit policy wins.
        for p in self.policies:
            try:
                matched = p.applies(agent, tool, args, context)
            except Exception:
                # A policy that errors must not fail open.
                return Decision(PolicyEffect.DENY,
                                f"policy '{p.name}' errored during evaluation")
            if matched:
                return Decision(p.effect, f"policy '{p.name}' v{p.version}: {p.reason}",
                                list(p.approvers))
        # 3. Deny-by-default for mutations; read-only is allowed.
        if tool.side_effect:
            return Decision(PolicyEffect.DENY,
                            f"no policy permits mutating tool '{tool.name}' (deny-by-default)")
        return Decision(PolicyEffect.ALLOW, "read-only tool, default allow")


def default_policies() -> list[Policy]:
    """Reference ruleset used by the example workflow."""
    return [
        Policy(
            name="read-only-ok", version=1, priority=10,
            applies=lambda a, t, args, ctx: not t.side_effect,
            effect=PolicyEffect.ALLOW, reason="read-only tools are always permitted",
        ),
        Policy(
            name="large-ap-entry-needs-approval", version=1, priority=20,
            applies=lambda a, t, args, ctx: (
                t.name == "create_ap_entry" and float(args.get("amount", 0)) > 5000),
            effect=PolicyEffect.REQUIRE_APPROVAL,
            reason="AP entries over $5,000 require finance approval",
            approvers=["finance-team"],
        ),
        Policy(
            name="small-ap-entry-ok", version=1, priority=30,
            applies=lambda a, t, args, ctx: t.name == "create_ap_entry",
            effect=PolicyEffect.ALLOW, reason="AP entries at or under $5,000 are auto-approved",
        ),
    ]
