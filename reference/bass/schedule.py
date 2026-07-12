"""Scheduled (cron) triggers.

Completes the trigger model (event and manual triggers are handled by the
orchestrator; this adds schedules). A `Scheduler.tick(now)` fires every schedule
whose cron expression matches the current minute. Each firing uses a
deterministic idempotency key (`sched:<id>:<minute>`), so a scheduler that runs
more than once in the same minute — or several scheduler replicas — still
produces exactly one run, reusing the orchestrator's dedup.

The cron matcher supports the standard 5 fields (minute hour day-of-month month
day-of-week) with `*`, `*/n`, `a-b`, `a,b`, and literals, including cron's
day-of-month/day-of-week OR semantics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .models import new_id


def _parse_field(field_expr: str, lo: int, hi: int) -> set[int]:
    values: set[int] = set()
    for part in field_expr.split(","):
        step = 1
        rng = part
        if "/" in part:
            rng, step_s = part.split("/", 1)
            step = int(step_s)
        if rng == "*":
            start, end = lo, hi
        elif "-" in rng:
            a, b = rng.split("-", 1)
            start, end = int(a), int(b)
        else:
            start = end = int(rng)
        for v in range(start, end + 1, step):
            if lo <= v <= hi:
                values.add(v)
    return values


def cron_matches(expr: str, dt: datetime) -> bool:
    """True if the 5-field cron expression matches datetime `dt` (minute grain)."""
    parts = expr.split()
    if len(parts) != 5:
        raise ValueError(f"cron expression must have 5 fields: {expr!r}")
    minute, hour, dom, month, dow = parts
    if dt.minute not in _parse_field(minute, 0, 59):
        return False
    if dt.hour not in _parse_field(hour, 0, 23):
        return False
    if dt.month not in _parse_field(month, 1, 12):
        return False

    # Day-of-month / day-of-week: if both are restricted, cron matches when
    # EITHER matches; if only one is restricted, that one must match.
    cron_dow = (dt.weekday() + 1) % 7            # Python Mon=0..Sun=6 → cron Sun=0..Sat=6
    dom_ok = dt.day in _parse_field(dom, 1, 31)
    dow_ok = cron_dow in _parse_field(dow, 0, 6)
    dom_restricted = dom.strip() != "*"
    dow_restricted = dow.strip() != "*"
    if dom_restricted and dow_restricted:
        return dom_ok or dow_ok
    if dom_restricted:
        return dom_ok
    if dow_restricted:
        return dow_ok
    return True


@dataclass
class Schedule:
    tenant_id: str
    workflow_name: str
    cron: str
    enabled: bool = True
    id: str = field(default_factory=lambda: new_id("sched"))


class Scheduler:
    """Fires due schedules for a given minute. `fire(schedule, trigger)` performs
    the firing (typically Orchestrator.fire) and returns the run."""

    def __init__(self, schedules: list[Schedule]):
        self.schedules = schedules

    def due(self, now: datetime) -> list[Schedule]:
        return [s for s in self.schedules if s.enabled and cron_matches(s.cron, now)]

    def tick(self, now: datetime, fire: Callable[[Schedule, dict], object]) -> list:
        slot = now.strftime("%Y-%m-%dT%H:%M")
        runs = []
        for s in self.due(now):
            trigger = {"source": "schedule", "schedule_id": s.id,
                       "fired_for": slot,
                       # deterministic key → once-per-minute dedup across replicas
                       "message_id": f"sched:{s.id}:{slot}"}
            runs.append(fire(s, trigger))
        return runs
