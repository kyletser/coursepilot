from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from .budget import AgentLimits, BudgetTracker
from .errors import AgentBudgetExceeded
from .grounding import (
    EvidencePolicy,
    GenerationPrompt,
    bind_claims_to_citations,
    build_citations,
    build_generation_prompt,
    evidence_only_summary,
    render_claims,
    verify_grounding,
)
from .guard import InputGuard
from .routing import DeterministicIntentRouter, ValidatedIntentRouter
from .schemas import (
    AgentRequest,
    AgentResponse,
    AgentStatus,
    AnswerDraft,
    Evidence,
    GroundingReport,
    IntentRoute,
)

INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
INSUFFICIENT_EVIDENCE_MESSAGE = "课程资料中没有足够依据回答该问题。"


class ChatAdapter(Protocol):
    def generate(self, prompt: GenerationPrompt) -> Any: ...


class EvidenceRetriever(Protocol):
    def search_course_material(
        self,
        *,
        course_id: str,
        query: str,
        filters: Mapping[str, Any],
        top_k: int,
    ) -> Any: ...


class QueryRewriteAdapter(Protocol):
    def rewrite(self, *, query: str, route: IntentRoute) -> Any: ...


class BusinessIntentHandler(Protocol):
    def handle(self, *, request: AgentRequest, route: IntentRoute) -> Any: ...


class TrustedAgentCore:
    """Dependency-light trusted core suitable for a later LangGraph wrapper.

    The core owns policy and final rendering. Adapters can classify, retrieve,
    rewrite, or phrase cited claims, but they cannot select arbitrary tools, raise
    budgets, manufacture citations, or add uncited answer text.
    """

    def __init__(
        self,
        *,
        router: ValidatedIntentRouter | None = None,
        retriever: EvidenceRetriever | None = None,
        query_rewriter: QueryRewriteAdapter | None = None,
        business_handler: BusinessIntentHandler | None = None,
        chat_adapter: ChatAdapter | None = None,
        evidence_policy: EvidencePolicy | None = None,
        input_guard: InputGuard | None = None,
        limits: AgentLimits | None = None,
        top_k: int = 8,
    ) -> None:
        if not 1 <= top_k <= 8:
            raise ValueError("top_k must be between 1 and 8")
        self.router = router or ValidatedIntentRouter()
        self.retriever = retriever
        self.query_rewriter = query_rewriter
        self.business_handler = business_handler
        self.chat_adapter = chat_adapter
        self.evidence_policy = evidence_policy or EvidencePolicy()
        self.input_guard = input_guard or InputGuard()
        self.limits = limits or AgentLimits()
        self.top_k = top_k

    async def run(
        self,
        request: AgentRequest,
        *,
        evidence: Sequence[Evidence | Mapping[str, Any]] | None = None,
    ) -> AgentResponse:
        budget = BudgetTracker(self.limits)
        accepted: list[Evidence] = []
        route: IntentRoute | None = None
        rewritten_query: str | None = None
        warnings: list[str] = []

        try:
            budget.record_step("input_guard")
            guard_result = self.input_guard.inspect(request)
            request = guard_result.request
            warnings.extend(guard_result.security_signals)

            budget.record_step("route_intent")
            route = await self.router.route(request)

            if self.business_handler is not None:
                budget.record_step("handle_business_intent")
                budget.record_tool_call(f"handle_{route.intent.value.lower()}")
                handled = self.business_handler.handle(request=request, route=route)
                if inspect.isawaitable(handled):
                    handled = await handled
                if handled is not None:
                    if not isinstance(handled, AgentResponse):
                        handled = AgentResponse.model_validate(handled)
                    return handled.model_copy(
                        update={
                            "route": route,
                            "budget": budget.snapshot(),
                            "warnings": [*warnings, *handled.warnings],
                        }
                    )

            if not route.needs_retrieval:
                return AgentResponse(
                    status=AgentStatus.ROUTED,
                    route=route,
                    answer=(f"请求已路由到 {route.intent.value} 的确定性业务处理器。"),
                    budget=budget.snapshot(),
                    warnings=warnings,
                )

            if request.active_index_version is None:
                return self._insufficient_response(
                    route=route,
                    budget=budget,
                    accepted=[],
                    rewritten_query=None,
                    warnings=[*warnings, "ACTIVE_INDEX_VERSION_REQUIRED"],
                )

            if evidence is None:
                try:
                    collected = await self._retrieve(
                        request.course_id,
                        request.active_index_version,
                        request.query,
                        budget,
                    )
                except AgentBudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001 - retrieval must fail closed
                    return self._retrieval_failure_response(
                        route=route,
                        budget=budget,
                        accepted=[],
                        rewritten_query=None,
                        warnings=warnings,
                        exception=exc,
                    )
            else:
                collected = self._coerce_evidence(evidence)

            budget.record_step("grade_evidence")
            grade = self.evidence_policy.grade(
                collected,
                course_id=request.course_id,
                active_index_version=request.active_index_version,
            )
            accepted = grade.accepted

            if not grade.sufficient and self.retriever is not None:
                budget.record_step("rewrite_query")
                budget.record_query_rewrite()
                rewritten_query = await self._rewrite(request.query, route)
                try:
                    collected = await self._retrieve(
                        request.course_id,
                        request.active_index_version,
                        rewritten_query,
                        budget,
                    )
                except AgentBudgetExceeded:
                    raise
                except Exception as exc:  # noqa: BLE001 - retrieval must fail closed
                    return self._retrieval_failure_response(
                        route=route,
                        budget=budget,
                        accepted=accepted,
                        rewritten_query=rewritten_query,
                        warnings=warnings,
                        exception=exc,
                    )
                budget.record_step("grade_rewritten_evidence")
                grade = self.evidence_policy.grade(
                    collected,
                    course_id=request.course_id,
                    active_index_version=request.active_index_version,
                )
                accepted = grade.accepted

            if not grade.sufficient:
                return self._insufficient_response(
                    route=route,
                    budget=budget,
                    accepted=accepted,
                    rewritten_query=rewritten_query,
                    warnings=warnings,
                )

            if self.chat_adapter is None:
                budget.record_step("generate_evidence_summary")
                return self._summary_response(
                    route=route,
                    budget=budget,
                    accepted=accepted,
                    rewritten_query=rewritten_query,
                    warnings=warnings,
                )

            base_citations = build_citations(accepted)
            feedback: list[str] = []
            last_grounding: GroundingReport | None = None
            for _attempt in range(2):
                budget.record_step("generate_response")
                prompt = build_generation_prompt(
                    query=rewritten_query or request.query,
                    route=route,
                    evidence=accepted,
                    grounding_feedback=feedback,
                )
                try:
                    draft = await self._generate(prompt)
                except Exception as exc:  # noqa: BLE001 - adapter failure is a fallback
                    feedback = [f"Adapter output failed schema validation: {exc}"]
                    continue

                if not draft.claims:
                    return self._insufficient_response(
                        route=route,
                        budget=budget,
                        accepted=accepted,
                        rewritten_query=rewritten_query,
                        warnings=[*warnings, "MODEL_ABSTAINED"],
                    )

                citations = bind_claims_to_citations(draft.claims, base_citations)
                citations = [item for item in citations if item.claim_indices]
                budget.record_step("verify_grounding")
                last_grounding = verify_grounding(
                    draft.claims,
                    citations,
                    accepted,
                    course_id=request.course_id,
                    active_index_version=request.active_index_version,
                )
                if last_grounding.valid:
                    return AgentResponse(
                        status=AgentStatus.ANSWERED,
                        route=route,
                        answer=render_claims(draft.claims),
                        claims=draft.claims,
                        citations=citations,
                        evidence=accepted,
                        rewritten_query=rewritten_query,
                        grounding=last_grounding,
                        budget=budget.snapshot(),
                        warnings=warnings,
                    )
                feedback = last_grounding.errors

            warnings.append("GENERATION_GROUNDING_FAILED")
            return self._summary_response(
                route=route,
                budget=budget,
                accepted=accepted,
                rewritten_query=rewritten_query,
                warnings=warnings,
                grounding=last_grounding,
            )
        except AgentBudgetExceeded as exc:
            if route is None:
                # The default hard limits always permit guard + routing; this protects
                # custom lower limits without fabricating a route.
                route = DeterministicIntentRouter().route(request)
            if accepted:
                answer, claims, citations = evidence_only_summary(accepted)
            else:
                answer, claims, citations = (
                    "Agent 已达到安全执行上限，且未收集到可验证证据。",
                    [],
                    [],
                )
            return AgentResponse(
                status=AgentStatus.BUDGET_EXCEEDED,
                route=route,
                answer=answer,
                claims=claims,
                citations=citations,
                evidence=accepted,
                error_code=exc.code,
                error_message=str(exc),
                rewritten_query=rewritten_query,
                budget=budget.snapshot(),
                warnings=warnings,
            )

    async def _retrieve(
        self,
        course_id: str,
        active_index_version: str,
        query: str,
        budget: BudgetTracker,
    ) -> list[Evidence]:
        if self.retriever is None:
            return []
        budget.record_step("retrieve_evidence")
        budget.record_tool_call("search_course_material")
        raw = self.retriever.search_course_material(
            course_id=course_id,
            query=query,
            filters={"index_version": active_index_version},
            top_k=self.top_k,
        )
        if inspect.isawaitable(raw):
            raw = await raw
        return self._coerce_evidence(raw)

    async def _rewrite(self, query: str, route: IntentRoute) -> str:
        if self.query_rewriter is None:
            targets = " ".join(route.target_concepts)
            return " ".join(part for part in (query.strip(), targets) if part)
        try:
            raw = self.query_rewriter.rewrite(query=query, route=route)
            if inspect.isawaitable(raw):
                raw = await raw
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("query rewrite adapter returned an invalid query")
            return raw.strip()[:4000]
        except Exception:  # noqa: BLE001 - rewrite failure uses deterministic fallback
            targets = " ".join(route.target_concepts)
            return " ".join(part for part in (query.strip(), targets) if part)

    async def _generate(self, prompt: GenerationPrompt) -> AnswerDraft:
        assert self.chat_adapter is not None
        raw = self.chat_adapter.generate(prompt)
        if inspect.isawaitable(raw):
            raw = await raw
        if isinstance(raw, AnswerDraft):
            return raw
        if isinstance(raw, str):
            return AnswerDraft.model_validate_json(raw)
        if isinstance(raw, Mapping):
            return AnswerDraft.model_validate(dict(raw))
        return AnswerDraft.model_validate(raw)

    @staticmethod
    def _coerce_evidence(
        raw: Sequence[Evidence | Mapping[str, Any]] | Any,
    ) -> list[Evidence]:
        if raw is None:
            return []
        if isinstance(raw, (str, bytes, Mapping)):
            raise TypeError("retriever must return a sequence of evidence records")
        return [
            item if isinstance(item, Evidence) else Evidence.model_validate(item)
            for item in raw
        ]

    @staticmethod
    def _insufficient_response(
        *,
        route: IntentRoute,
        budget: BudgetTracker,
        accepted: Sequence[Evidence],
        rewritten_query: str | None,
        warnings: list[str],
    ) -> AgentResponse:
        return AgentResponse(
            status=AgentStatus.INSUFFICIENT_EVIDENCE,
            route=route,
            answer=INSUFFICIENT_EVIDENCE_MESSAGE,
            evidence=list(accepted),
            error_code=INSUFFICIENT_EVIDENCE,
            error_message=INSUFFICIENT_EVIDENCE_MESSAGE,
            rewritten_query=rewritten_query,
            budget=budget.snapshot(),
            warnings=warnings,
        )

    @staticmethod
    def _summary_response(
        *,
        route: IntentRoute,
        budget: BudgetTracker,
        accepted: Sequence[Evidence],
        rewritten_query: str | None,
        warnings: list[str],
        grounding: GroundingReport | None = None,
    ) -> AgentResponse:
        answer, claims, citations = evidence_only_summary(accepted)
        return AgentResponse(
            status=AgentStatus.EVIDENCE_SUMMARY,
            route=route,
            answer=answer,
            claims=claims,
            citations=citations,
            evidence=list(accepted),
            rewritten_query=rewritten_query,
            grounding=grounding,
            budget=budget.snapshot(),
            warnings=warnings,
        )

    @staticmethod
    def _retrieval_failure_response(
        *,
        route: IntentRoute,
        budget: BudgetTracker,
        accepted: Sequence[Evidence],
        rewritten_query: str | None,
        warnings: list[str],
        exception: Exception,
    ) -> AgentResponse:
        if accepted:
            answer, claims, citations = evidence_only_summary(accepted)
        else:
            answer, claims, citations = "课程资料检索当前不可用。", [], []
        return AgentResponse(
            status=AgentStatus.RETRIEVAL_UNAVAILABLE,
            route=route,
            answer=answer,
            claims=claims,
            citations=citations,
            evidence=list(accepted),
            error_code=getattr(exception, "code", "RETRIEVAL_UNAVAILABLE"),
            error_message="课程资料检索当前不可用。",
            rewritten_query=rewritten_query,
            budget=budget.snapshot(),
            warnings=[*warnings, type(exception).__name__],
        )
