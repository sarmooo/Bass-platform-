"""The policy engine — the safety gate.

Every tool call passes through `PolicyEngine.check` before it executes. This is the
single place where "may this happen?" is decided, returning ALLOW, DENY, or
REQUIRE_APPROVAL with a human-readable reason (which is logged).

Policies here are simple Python predicates for readability; a real system would use
declarative, versioned rules (see docs/05-security-governance.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .models import Agent, PolicyEffect, Tool


@dataclass
class Decision:
    effect: PolicyEffect
    reason: str
    approvers: list[str] | None = None


@dataclass
class Policy:
    name: str
    # predicate(agent, tool, args, context) -> bool : does this policy apply?
    applies: Callable[[Agent, Tool, dict, dict], bool]
    effect: PolicyEffect
    reason: str
    approvers: list[str] | None = None
    priority: int = 100   # lower number = evaluated first


class PolicyEngine:
    """Evaluates ordered policies; first match wins. Fails closed on mutations."""

    def __init__(self, policies: list[Policy]):
        self.policies = sorted(policies, key=lambda p: p.priority)

    def check(self, agent: Agent, tool: Tool, args: dict, context: dict) -> Decision:
        # 1. The agent may only use tools on its allow-list (least privilege).
        if tool.name not in agent.tools:
            return Decision(PolicyEffect.DENY,
                            f"'{tool.name}' is not in {agent.name}'s allow-list")

        # 2. First matching explicit policy wins.
        for p in self.policies:
            if p.applies(agent, tool, args, context):
                return Decision(p.effect, f"policy '{p.name}': {p.reason}", p.approvers)

        # 3. Default posture: read-only allowed, mutations denied-by-default.
        if tool.side_effect:
            return Decision(PolicyEffect.DENY,
                            f"no policy permits mutating tool '{tool.name}' (deny-by-default)")
        return Decision(PolicyEffect.ALLOW, "read-only tool, default allow")


# --- a few example policies used by the demo --------------------------------

def default_policies() -> list[Policy]:
    return [
        Policy(
            name="read-only-ok",
            applies=lambda a, t, args, ctx: not t.side_effect,
            effect=PolicyEffect.ALLOW,
            reason="read-only tools are always permitted",
            priority=10,
        ),
        Policy(
            name="large-ap-entry-needs-approval",
            applies=lambda a, t, args, ctx: (
                t.name == "create_ap_entry" and float(args.get("amount", 0)) > 5000
            ),
            effect=PolicyEffect.REQUIRE_APPROVAL,
            reason="AP entries over $5,000 require finance approval",
            approvers=["finance-team"],
            priority=20,
        ),
        Policy(
            name="small-ap-entry-ok",
            applies=lambda a, t, args, ctx: t.name == "create_ap_entry",
            effect=PolicyEffect.ALLOW,
            reason="AP entries at or under $5,000 are auto-approved",
            priority=30,
        ),
    ]
