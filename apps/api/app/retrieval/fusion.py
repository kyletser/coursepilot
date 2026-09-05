from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .types import DenseCandidate, FusedCandidate, LexicalCandidate

RankedHit = DenseCandidate | LexicalCandidate


@dataclass(slots=True)
class _Accumulator:
    chunk_id: str
    rrf_score: float = 0.0
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    dense_score: float | None = None
    lexical_score: float | None = None
    dense_rank: int | None = None
    lexical_rank: int | None = None


def _deduplicate_ranked(candidates: Sequence[RankedHit]) -> list[RankedHit]:
    seen: set[str] = set()
    result: list[RankedHit] = []
    for candidate in candidates:
        chunk_id = candidate.chunk_id
        if chunk_id not in seen:
            seen.add(chunk_id)
            result.append(candidate)
    return result


def reciprocal_rank_fusion(
    dense: Sequence[DenseCandidate],
    lexical: Sequence[LexicalCandidate],
    *,
    k: int = 60,
    top_k: int | None = 20,
) -> list[FusedCandidate]:
    """Fuse ranked dense and lexical hits with deterministic RRF de-duplication.

    Input order is the source rank. Duplicate IDs inside a route are removed
    before assigning ranks, so a malformed duplicate cannot penalize later hits.
    """

    if k < 1:
        raise ValueError("k must be at least 1")
    if top_k is not None and top_k < 1:
        raise ValueError("top_k must be at least 1 when provided")

    accumulators: dict[str, _Accumulator] = {}
    for source, candidates in (
        ("dense", _deduplicate_ranked(dense)),
        ("lexical", _deduplicate_ranked(lexical)),
    ):
        for rank, candidate in enumerate(candidates, start=1):
            current = accumulators.setdefault(
                candidate.chunk_id, _Accumulator(chunk_id=candidate.chunk_id)
            )
            current.rrf_score += 1.0 / (k + rank)
            if not current.content and candidate.content:
                current.content = candidate.content
            # Dense metadata is established first; lexical-only keys are added.
            for key, value in candidate.metadata.items():
                current.metadata.setdefault(key, value)
            if source == "dense":
                current.dense_rank = rank
                current.dense_score = candidate.score
            else:
                current.lexical_rank = rank
                current.lexical_score = candidate.score

    fused = [
        FusedCandidate(
            chunk_id=item.chunk_id,
            rrf_score=item.rrf_score,
            content=item.content,
            metadata=item.metadata,
            dense_score=item.dense_score,
            lexical_score=item.lexical_score,
            dense_rank=item.dense_rank,
            lexical_rank=item.lexical_rank,
        )
        for item in accumulators.values()
    ]
    fused.sort(
        key=lambda item: (
            -item.rrf_score,
            min(item.dense_rank or 10**9, item.lexical_rank or 10**9),
            item.chunk_id,
        )
    )
    return fused if top_k is None else fused[:top_k]
