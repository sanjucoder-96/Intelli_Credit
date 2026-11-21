"""Per-node resource budgets — the circuit breaker, not a ledger (ADR-0005).

A :class:`BudgetMeter` counts every charge and raises before the budget is
exceeded, so a runaway loop can never become an unbounded bill. Phase 5 wires
LLM-call cost accounting through this meter from the gateway.
"""

from __future__ import annotations

from app.agents.spec import StepBudget
from app.core.errors import AppError


class BudgetExceededError(AppError):
    """A step attempted to spend beyond its configured budget."""

    code = "budget_exceeded"
    status_code = 409


class BudgetMeter:
    """Counts tool/LLM spend against a :class:`StepBudget` and enforces it."""

    def __init__(self, budget: StepBudget) -> None:
        self._budget = budget
        self._tool_calls = 0
        self._llm_calls = 0
        self._cost_usd = 0.0

    def charge_tool(self) -> None:
        """Record one tool call; raise if the tool budget is exhausted."""
        if self._tool_calls + 1 > self._budget.max_tool_calls:
            raise BudgetExceededError(
                "Tool-call budget exhausted",
                details={"max_tool_calls": self._budget.max_tool_calls},
            )
        self._tool_calls += 1

    def charge_llm(self, cost_usd: float = 0.0) -> None:
        """Record one LLM call (optionally with cost); raise when exhausted."""
        if self._llm_calls + 1 > self._budget.max_llm_calls:
            raise BudgetExceededError(
                "LLM-call budget exhausted",
                details={"max_llm_calls": self._budget.max_llm_calls},
            )
        if self._cost_usd + cost_usd > self._budget.max_cost_usd:
            raise BudgetExceededError(
                "Cost budget exhausted",
                details={"max_cost_usd": self._budget.max_cost_usd},
            )
        self._llm_calls += 1
        self._cost_usd += cost_usd

    def snapshot(self) -> dict[str, float]:
        """Current spend counters (for traces and the run cost roll-up)."""
        return {
            "tool_calls": float(self._tool_calls),
            "llm_calls": float(self._llm_calls),
            "cost_usd": self._cost_usd,
        }
