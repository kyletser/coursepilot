from __future__ import annotations

import re

from .errors import InputGuardError
from .schemas import AgentRequest, GuardResult

_DISALLOWED_CONTROL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_PROMPT_OVERRIDE_PATTERNS = (
    re.compile(
        r"\b(?:ignore|disregard)\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|system)\s+(?:instructions?|rules?|prompts?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:reveal|print|show)\s+(?:the\s+)?system\s+prompt\b",
        re.IGNORECASE,
    ),
    re.compile(r"忽略(?:以上|此前|之前|所有|系统).{0,12}(?:指令|规则|提示词)"),
    re.compile(r"(?:泄露|显示|输出).{0,8}(?:系统提示词|系统指令)"),
)


class InputGuard:
    """Validate the request envelope before routing or invoking dependencies.

    Prompt-override language is recorded, not interpreted. The trusted core never
    grants user text authority over routing, tools, budgets, or system instructions.
    """

    def inspect(self, request: AgentRequest) -> GuardResult:
        if _DISALLOWED_CONTROL_CHARACTERS.search(request.query):
            raise InputGuardError("Query contains disallowed control characters")

        signals = [
            "PROMPT_OVERRIDE_LANGUAGE"
            for pattern in _PROMPT_OVERRIDE_PATTERNS
            if pattern.search(request.query)
        ]
        # A single stable signal is enough even if several patterns match.
        return GuardResult(
            request=request,
            security_signals=list(dict.fromkeys(signals)),
        )

    def validate(self, request: AgentRequest) -> AgentRequest:
        return self.inspect(request).request
