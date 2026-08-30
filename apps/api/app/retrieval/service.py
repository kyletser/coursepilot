from __future__ import annotations

import asyncio
import inspect
import math
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from .fusion import reciprocal_rank_fusion
from .types import (
    CandidateTrace,
    DenseCandidate,
    FusedCandidate,
    LexicalCandidate,
    RetrievalResult,
    RetrievalStageTrace,
    RetrievalTrace,
    RetrievalUnavailableError,
)


class DenseRetriever(Protocol):
    def search(
        self,
        *,
        course_id: str,
        index_version: str,
        query: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[DenseCandidate] | Any: ...


class LexicalRetriever(Protocol):
    def search(
        self,
        *,
        course_id: str,
        index_version: str,
        query: str,
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[LexicalCandidate] | Any: ...


class Reranker(Protocol):
    def score(self, query: str, documents: Sequence[str]) -> Sequence[float] | Any: ...


@dataclass(frozen=True, slots=True)
class HybridRetrievalConfig:
    route_top_k: int = 20
    fusion_top_k: int = 20
    final_top_k: int = 5
    rrf_k: int = 60
    reranker_timeout_seconds: float = 15.0

    def __post_init__(self) -> None:
        if min(self.route_top_k, self.fusion_top_k, self.final_top_k) < 1:
            raise ValueError("all top-k values must be at least 1")
        if self.rrf_k < 1:
            raise ValueError("rrf_k must be at least 1")
        if self.reranker_timeout_seconds <= 0:
            raise ValueError("reranker timeout must be positive")


T = TypeVar("T")


async def _invoke(method: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Call sync or async plugins without blocking the event loop."""

    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    value = await asyncio.to_thread(method, *args, **kwargs)
    if inspect.isawaitable(value):
        return await value
    return value


def _safe_error_message(exception: Exception) -> str:
    message = str(exception).replace("\r", " ").replace("\n", " ").strip()
    return message[:240] or type(exception).__name__


def _route_candidate_traces(
    candidates: Sequence[DenseCandidate] | Sequence[LexicalCandidate], source: str
) -> list[CandidateTrace]:
    return [
        CandidateTrace(
            chunk_id=candidate.chunk_id,
            rank=rank,
            score=candidate.score,
            source=source,
        )
        for rank, candidate in enumerate(candidates, start=1)
    ]


def _fused_candidate_traces(
    candidates: Sequence[FusedCandidate], *, source: str, use_output_score: bool
) -> list[CandidateTrace]:
    return [
        CandidateTrace(
            chunk_id=candidate.chunk_id,
            rank=rank,
            score=candidate.score if use_output_score else candidate.rrf_score,
            source=source,
            dense_rank=candidate.dense_rank,
            lexical_rank=candidate.lexical_rank,
            dense_score=candidate.dense_score,
            lexical_score=candidate.lexical_score,
        )
        for rank, candidate in enumerate(candidates, start=1)
    ]


class HybridRetriever:
    def __init__(
        self,
        *,
        dense: DenseRetriever | None,
        lexical: LexicalRetriever | None,
        reranker: Reranker | None = None,
        config: HybridRetrievalConfig | None = None,
    ) -> None:
        self.dense = dense
        self.lexical = lexical
        self.reranker = reranker
        self.config = config or HybridRetrievalConfig()

    async def _run_route(
        self,
        *,
        route: str,
        provider: DenseRetriever | LexicalRetriever | None,
        expected_type: type[T],
        trace: RetrievalStageTrace,
        course_id: str,
        index_version: str,
        query: str,
        filters: Mapping[str, Any] | None,
    ) -> list[T] | None:
        started = time.perf_counter()
        if provider is None:
            trace.status = "unavailable"
            trace.error_type = "NotConfigured"
            trace.error_message = f"{route} retriever is not configured"
            trace.latency_ms = (time.perf_counter() - started) * 1000
            return None
        try:
            raw_candidates = await _invoke(
                provider.search,
                course_id=course_id,
                index_version=index_version,
                query=query,
                top_k=self.config.route_top_k,
                filters=filters,
            )
            if isinstance(raw_candidates, (str, bytes)):
                raise TypeError(f"{route} retriever must return a candidate sequence")
            candidates = list(raw_candidates)
            if any(
                not isinstance(candidate, expected_type) for candidate in candidates
            ):
                raise TypeError(f"{route} retriever returned an invalid candidate type")
            trace.status = "success"
            trace.candidates = _route_candidate_traces(candidates, route)
            return candidates
        except Exception as exc:  # noqa: BLE001 - plugin failure triggers degradation
            trace.status = "failed"
            trace.error_type = type(exc).__name__
            trace.error_message = _safe_error_message(exc)
            return None
        finally:
            trace.latency_ms = (time.perf_counter() - started) * 1000

    async def retrieve(
        self,
        *,
        course_id: str,
        index_version: str,
        query: str,
        top_k: int | None = None,
        filters: Mapping[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> RetrievalResult:
        if not course_id or not index_version:
            raise ValueError("course_id and index_version are required")
        if not query or not query.strip():
            raise ValueError("query must not be empty")
        final_top_k = self.config.final_top_k if top_k is None else top_k
        if final_top_k < 1:
            raise ValueError("top_k must be at least 1")

        started = time.perf_counter()
        trace = RetrievalTrace(
            trace_id=trace_id or str(uuid.uuid4()),
            course_id=str(course_id),
            index_version=str(index_version),
            query=query,
        )

        dense_candidates, lexical_candidates = await asyncio.gather(
            self._run_route(
                route="dense",
                provider=self.dense,
                expected_type=DenseCandidate,
                trace=trace.dense,
                course_id=str(course_id),
                index_version=str(index_version),
                query=query,
                filters=filters,
            ),
            self._run_route(
                route="lexical",
                provider=self.lexical,
                expected_type=LexicalCandidate,
                trace=trace.lexical,
                course_id=str(course_id),
                index_version=str(index_version),
                query=query,
                filters=filters,
            ),
        )

        if dense_candidates is None:
            trace.degraded_routes.append("dense")
        if lexical_candidates is None:
            trace.degraded_routes.append("lexical")
        if dense_candidates is None and lexical_candidates is None:
            trace.fusion.status = "blocked"
            trace.reranker.status = "blocked"
            trace.final.status = "failed"
            trace.total_latency_ms = (time.perf_counter() - started) * 1000
            raise RetrievalUnavailableError(trace)

        fusion_started = time.perf_counter()
        fused = reciprocal_rank_fusion(
            dense_candidates or (),
            lexical_candidates or (),
            k=self.config.rrf_k,
            top_k=self.config.fusion_top_k,
        )
        trace.fusion.status = "success"
        trace.fusion.candidates = _fused_candidate_traces(
            fused, source="rrf", use_output_score=False
        )
        trace.fusion.latency_ms = (time.perf_counter() - fusion_started) * 1000

        ranked = fused
        rerank_started = time.perf_counter()
        if not fused:
            trace.reranker.status = "skipped_empty"
        elif self.reranker is None:
            trace.reranker.status = "skipped_not_configured"
        else:
            try:
                raw_scores = await asyncio.wait_for(
                    _invoke(
                        self.reranker.score,
                        query,
                        tuple(candidate.content for candidate in fused),
                    ),
                    timeout=self.config.reranker_timeout_seconds,
                )
                scores = [float(score) for score in raw_scores]
                if len(scores) != len(fused) or any(
                    not math.isfinite(score) for score in scores
                ):
                    raise ValueError("reranker returned invalid scores")
                ranked = [
                    candidate.with_rerank_score(score)
                    for candidate, score in zip(fused, scores, strict=True)
                ]
                ranked.sort(
                    key=lambda candidate: (
                        -candidate.score,
                        -candidate.rrf_score,
                        candidate.chunk_id,
                    )
                )
                trace.reranker.status = "success"
                trace.reranker.candidates = _fused_candidate_traces(
                    ranked, source="reranker", use_output_score=True
                )
            except TimeoutError as exc:
                trace.reranker.status = "timeout_fallback"
                trace.reranker.error_type = type(exc).__name__
                trace.reranker.error_message = "reranker timed out; retained RRF order"
                trace.degraded_routes.append("reranker_timeout")
            except Exception as exc:  # noqa: BLE001 - retain RRF on plugin failure
                trace.reranker.status = "error_fallback"
                trace.reranker.error_type = type(exc).__name__
                trace.reranker.error_message = _safe_error_message(exc)
                trace.degraded_routes.append("reranker_error")
        trace.reranker.latency_ms = (time.perf_counter() - rerank_started) * 1000

        final_candidates = tuple(ranked[:final_top_k])
        trace.final.status = "success"
        trace.final.candidates = _fused_candidate_traces(
            final_candidates,
            source="reranker"
            if any(candidate.rerank_score is not None for candidate in final_candidates)
            else "rrf",
            use_output_score=True,
        )
        trace.total_latency_ms = (time.perf_counter() - started) * 1000
        return RetrievalResult(candidates=final_candidates, trace=trace)
