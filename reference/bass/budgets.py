"""Budget enforcement — the runaway-cost backstop.

A run carries a cost ceiling and a step ceiling. The engine consults the budget
before each step and after each cost is booked; exceeding either fails the run
closed (docs/05-security-governance.md § Safe failure).
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Run


@dataclass
class Budget:
    max_usd: float
    max_steps: int

    def check(self, run: Run) -> str | None:
        """Return a violation reason, or None if within budget."""
        if run.cost_usd > self.max_usd:
            return f"cost ${run.cost_usd:.4f} exceeds budget ${self.max_usd:.4f}"
        if run.steps_used > self.max_steps:
            return f"steps {run.steps_used} exceed budget {self.max_steps}"
        return None
