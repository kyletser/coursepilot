from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

RetrievalSource = Literal["dense", "lexical"]


def _validate_candidate(chunk_id: str, score: float) -> None:
    if not chunk_id or not chunk_id.strip():
        raise ValueError("chunk_id must not be empty")
    if not math.isfinite(score):
        raise ValueError("candidate score must be finite")


@dataclass(frozen=True, slots=True)
class DenseCandidate:
    """A ranked dense-search hit.

    ``content`` is carried into the reranker but is deliberately omitted from
    traces so course text is not copied into operational logs by default.
    """

    chunk_id: str
    score: float
    content: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_candidate(self.chunk_id, self.score)
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class LexicalCandidate:
    """A ranked lexical-search hit."""

    chunk_id: str
    score: float
    content: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_candidate(self.chunk_id, self.score)
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True, slots=True)
class FusedCandidate:
    """A de-duplicated RRF hit, optionally carrying a reranker score."""

    chunk_id: str
    rrf_score: float
    content: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)
    dense_score: float | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None
    rerank_score: float | None = None

    def __post_init__(self) -> None:
        _validate_candidate(self.chunk_id, self.rrf_score)
        for rank in (self.dense_rank, self.lexical_rank):
            if rank is not None and rank < 1:
                raise ValueError("source ranks are one-based")
        if self.rerank_score is not None and not math.isfinite(self.rerank_score):
            raise ValueError("rerank_score must be finite")
        object.__setattr__(self, "metadata", dict(self.metadata))

    @property
    def score(self) -> float:
        """The score governing the current output order."""

        return self.rerank_score if self.rerank_score is not None else self.rrf_score

    @property
    def sources(self) -> tuple[RetrievalSource, ...]:
        sources: list[RetrievalSource] = []
        if self.dense_rank is not None:
            sources.append("dense")
        if self.lexical_rank is not None:
            sources.append("lexical")
        return tuple(sources)

    def with_rerank_score(self, score: float) -> FusedCandidate:
        return replace(self, rerank_score=float(score))


@dataclass(frozen=True, slots=True)
class CandidateTrace:
    chunk_id: str
    rank: int
    score: float
    source: str
    dense_rank: int | None = None
    lexical_rank: int | None = None
    dense_score: float | None = None
    lexical_score: float | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "chunk_id": self.chunk_id,
            "rank": self.rank,
            "score": self.score,
            "source": self.source,
        }
        optional = {
            "dense_rank": self.dense_rank,
            "lexical_rank": self.lexical_rank,
            "dense_score": self.dense_score,
            "lexical_score": self.lexical_score,
        }
        result.update(
            {key: value for key, value in optional.items() if value is not None}
        )
        return result


@dataclass(slots=True)
class RetrievalStageTrace:
    name: str
    status: str = "pending"
    latency_ms: float = 0.0
    candidates: list[CandidateTrace] = field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "name": self.name,
            "status": self.status,
            "latency_ms": round(self.latency_ms, 3),
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }
        if self.error_type is not None:
            result["error_type"] = self.error_type
        if self.error_message is not None:
            result["error_message"] = self.error_message
        return result


@dataclass(slots=True)
class RetrievalTrace:
    trace_id: str
    course_id: str
    index_version: str
    query: str
    dense: RetrievalStageTrace = field(
        default_factory=lambda: RetrievalStageTrace(name="dense")
    )
    lexical: RetrievalStageTrace = field(
        default_factory=lambda: RetrievalStageTrace(name="lexical")
    )
    fusion: RetrievalStageTrace = field(
        default_factory=lambda: RetrievalStageTrace(name="rrf")
    )
    reranker: RetrievalStageTrace = field(
        default_factory=lambda: RetrievalStageTrace(name="reranker")
    )
    final: RetrievalStageTrace = field(
        default_factory=lambda: RetrievalStageTrace(name="final")
    )
    degraded_routes: list[str] = field(default_factory=list)
    total_latency_ms: float = 0.0

    @property
    def degraded(self) -> bool:
        return bool(self.degraded_routes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "course_id": self.course_id,
            "index_version": self.index_version,
            "query": self.query,
            "degraded": self.degraded,
            "degraded_routes": list(self.degraded_routes),
            "total_latency_ms": round(self.total_latency_ms, 3),
            "stages": {
                "dense": self.dense.to_dict(),
                "lexical": self.lexical.to_dict(),
                "rrf": self.fusion.to_dict(),
                "reranker": self.reranker.to_dict(),
                "final": self.final.to_dict(),
            },
        }


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    candidates: tuple[FusedCandidate, ...]
    trace: RetrievalTrace


class RetrievalUnavailableError(RuntimeError):
    """Raised when neither authoritative retrieval route can be queried."""

    code = "RETRIEVAL_UNAVAILABLE"

    def __init__(self, trace: RetrievalTrace) -> None:
        super().__init__("Dense and lexical retrieval are both unavailable")
        self.trace = trace
