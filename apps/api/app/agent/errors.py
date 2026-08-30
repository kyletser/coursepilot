from __future__ import annotations


class AgentCoreError(RuntimeError):
    """Base error for deterministic Agent boundary failures."""

    code = "AGENT_CORE_ERROR"

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code or self.code


class InputGuardError(AgentCoreError):
    code = "AGENT_INPUT_REJECTED"


class AgentBudgetExceeded(AgentCoreError):
    code = "AGENT_BUDGET_EXCEEDED"

    def __init__(self, budget_name: str, limit: int) -> None:
        self.budget_name = budget_name
        self.limit = limit
        super().__init__(f"Agent {budget_name} budget of {limit} was exhausted")
