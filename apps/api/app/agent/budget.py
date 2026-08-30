from __future__ import annotations

from dataclasses import dataclass

from .errors import AgentBudgetExceeded
from .schemas import BudgetSnapshot


@dataclass(frozen=True, slots=True)
class AgentLimits:
    max_query_rewrites: int = 1
    max_tool_calls: int = 8
    max_steps: int = 12

    def __post_init__(self) -> None:
        if any(
            type(value) is not int
            for value in (
                self.max_query_rewrites,
                self.max_tool_calls,
                self.max_steps,
            )
        ):
            raise TypeError("Agent limits must be integers")
        if self.max_query_rewrites < 0:
            raise ValueError("max_query_rewrites must be non-negative")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must be non-negative")
        if self.max_steps < 1:
            raise ValueError("max_steps must be positive")
        # Product safety ceilings cannot be relaxed by runtime configuration.
        if self.max_query_rewrites > 1:
            raise ValueError("query rewrite hard limit is 1")
        if self.max_tool_calls > 8:
            raise ValueError("tool call hard limit is 8")
        if self.max_steps > 12:
            raise ValueError("step hard limit is 12")


class BudgetTracker:
    def __init__(self, limits: AgentLimits | None = None) -> None:
        self.limits = limits or AgentLimits()
        self.query_rewrites = 0
        self.tool_calls = 0
        self.steps = 0

    def record_step(self, _name: str) -> None:
        self._consume("steps", "step", self.limits.max_steps)

    def record_tool_call(self, _name: str) -> None:
        self._consume("tool_calls", "tool call", self.limits.max_tool_calls)

    def record_query_rewrite(self) -> None:
        self._consume("query_rewrites", "query rewrite", self.limits.max_query_rewrites)

    def _consume(self, attribute: str, budget_name: str, limit: int) -> None:
        current = getattr(self, attribute)
        if current >= limit:
            raise AgentBudgetExceeded(budget_name, limit)
        setattr(self, attribute, current + 1)

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            query_rewrites=self.query_rewrites,
            tool_calls=self.tool_calls,
            steps=self.steps,
            max_query_rewrites=self.limits.max_query_rewrites,
            max_tool_calls=self.limits.max_tool_calls,
            max_steps=self.limits.max_steps,
        )
