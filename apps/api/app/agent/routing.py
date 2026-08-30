from __future__ import annotations

import inspect
import re
from collections.abc import Mapping
from typing import Any, Protocol

from .schemas import AgentRequest, Intent, IntentRoute

_RETRIEVAL_INTENTS = {
    Intent.TUTOR_QA,
    Intent.CONCEPT_COMPARE,
    Intent.DIAGNOSE,
}

_INTENT_RULES: tuple[tuple[Intent, tuple[re.Pattern[str], ...]], ...] = (
    (
        Intent.LEARNING_PATH,
        (
            re.compile(r"(?:学习路径|学习顺序|复习顺序|先学什么|怎么学|学习计划)"),
            re.compile(
                r"\b(?:learning path|study plan|what .* learn first)\b",
                re.IGNORECASE,
            ),
        ),
    ),
    (
        Intent.QUIZ,
        (
            re.compile(r"(?:测验|测试|出题|考考我|练习题|做题)"),
            re.compile(r"\b(?:quiz|test me|practice questions?)\b", re.IGNORECASE),
        ),
    ),
    (
        Intent.DIAGNOSE,
        (
            re.compile(r"(?:诊断|薄弱|知识缺口|前置知识|哪里不会|为什么学不会)"),
            re.compile(
                r"\b(?:diagnos|knowledge gap|prerequisite|weakness)\w*\b",
                re.IGNORECASE,
            ),
        ),
    ),
    (
        Intent.CONCEPT_COMPARE,
        (
            re.compile(r"(?:比较|区别|差异|异同|对比|和.+有什么不同)"),
            re.compile(
                r"(?:\bvs\.?\b|\bversus\b|\bcompare\b|\bdifference between\b)",
                re.IGNORECASE,
            ),
        ),
    ),
)


class RouterAdapter(Protocol):
    def route(self, request: AgentRequest) -> Any: ...


class DeterministicIntentRouter:
    def route(self, request: AgentRequest) -> IntentRoute:
        intent = request.requested_intent or self._infer_intent(request.query)
        requested = request.requested_intent is not None
        reason = (
            "The caller supplied a validated intent override."
            if requested
            else f"Deterministic routing rules selected {intent.value}."
        )
        return IntentRoute(
            intent=intent,
            target_concepts=request.target_concepts,
            needs_retrieval=intent in _RETRIEVAL_INTENTS,
            reason=reason,
        )

    @staticmethod
    def _infer_intent(query: str) -> Intent:
        for intent, patterns in _INTENT_RULES:
            if any(pattern.search(query) for pattern in patterns):
                return intent
        return Intent.TUTOR_QA


class ValidatedIntentRouter:
    """Use an optional LLM classifier without trusting its output.

    Invalid JSON/schema, unsupported policy combinations, exceptions, and attempts to
    override an explicit caller intent all fall back to deterministic routing.
    """

    def __init__(
        self,
        adapter: RouterAdapter | None = None,
        *,
        deterministic: DeterministicIntentRouter | None = None,
    ) -> None:
        self.adapter = adapter
        self.deterministic = deterministic or DeterministicIntentRouter()

    async def route(self, request: AgentRequest) -> IntentRoute:
        fallback = self.deterministic.route(request)
        if self.adapter is None:
            return fallback

        try:
            raw = self.adapter.route(request)
            if inspect.isawaitable(raw):
                raw = await raw
            candidate = self._validate(raw)
            if (
                request.requested_intent
                and candidate.intent != request.requested_intent
            ):
                return fallback
            if candidate.needs_retrieval != (candidate.intent in _RETRIEVAL_INTENTS):
                return fallback
            if request.target_concepts:
                candidate = candidate.model_copy(
                    update={"target_concepts": request.target_concepts}
                )
            return candidate
        except Exception:  # noqa: BLE001 - an optional classifier is never authoritative
            # Router availability or malformed output must never block the baseline.
            return fallback

    @staticmethod
    def _validate(raw: Any) -> IntentRoute:
        if isinstance(raw, IntentRoute):
            return raw
        if isinstance(raw, str):
            return IntentRoute.model_validate_json(raw)
        if isinstance(raw, Mapping):
            return IntentRoute.model_validate(dict(raw))
        return IntentRoute.model_validate(raw)
