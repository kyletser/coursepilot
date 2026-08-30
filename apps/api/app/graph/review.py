from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.graph.domain import (
    ApprovedConcept,
    ApprovedRelation,
    ConceptCandidate,
    RelationCandidate,
    RelationType,
)
from app.graph.errors import GraphCycleError, GraphSelfLoopError
from app.graph.outbox import (
    GraphOutboxEvent,
    concept_approved_event,
    relation_approved_event,
)


@dataclass(frozen=True, slots=True)
class ConceptApproval:
    fact: ApprovedConcept
    outbox_event: GraphOutboxEvent


@dataclass(frozen=True, slots=True)
class RelationApproval:
    fact: ApprovedRelation
    outbox_event: GraphOutboxEvent


class GraphReviewService:
    """Deterministic review logic to execute inside a PostgreSQL transaction."""

    def approve_concept(
        self,
        candidate: ConceptCandidate,
        *,
        reviewer_id: uuid.UUID | str,
        reviewed_at: datetime | None = None,
        name: str | None = None,
        description: str | None = None,
        event_id: uuid.UUID | None = None,
    ) -> ConceptApproval:
        fact = ApprovedConcept(
            id=candidate.id,
            course_id=candidate.course_id,
            name=(name if name is not None else candidate.name).strip(),
            description=(
                description if description is not None else candidate.description
            ).strip(),
            source_chunk_id=candidate.source_chunk_id,
            confidence=candidate.confidence,
            reviewer_id=reviewer_id,
            reviewed_at=_review_time(reviewed_at),
        )
        return ConceptApproval(
            fact=fact,
            outbox_event=concept_approved_event(fact, event_id=event_id),
        )

    def approve_relation(
        self,
        candidate: RelationCandidate,
        *,
        approved_relations: Iterable[ApprovedRelation],
        reviewer_id: uuid.UUID | str,
        reviewed_at: datetime | None = None,
        relation_type: RelationType | str | None = None,
        event_id: uuid.UUID | None = None,
    ) -> RelationApproval:
        effective_candidate = RelationCandidate(
            id=candidate.id,
            course_id=candidate.course_id,
            from_concept_id=candidate.from_concept_id,
            to_concept_id=candidate.to_concept_id,
            relation_type=(
                relation_type if relation_type is not None else candidate.relation_type
            ),
            source_chunk_id=candidate.source_chunk_id,
            confidence=candidate.confidence,
        )
        existing = tuple(approved_relations)
        assert_prerequisite_publishable(effective_candidate, existing)
        fact = ApprovedRelation(
            id=effective_candidate.id,
            course_id=effective_candidate.course_id,
            from_concept_id=effective_candidate.from_concept_id,
            to_concept_id=effective_candidate.to_concept_id,
            relation_type=effective_candidate.relation_type,
            source_chunk_id=effective_candidate.source_chunk_id,
            confidence=effective_candidate.confidence,
            reviewer_id=reviewer_id,
            reviewed_at=_review_time(reviewed_at),
        )
        return RelationApproval(
            fact=fact,
            outbox_event=relation_approved_event(fact, event_id=event_id),
        )


def assert_prerequisite_publishable(
    candidate: RelationCandidate,
    approved_relations: Iterable[ApprovedRelation],
) -> None:
    """Reject a prerequisite edge if it is a self-loop or closes a course-local cycle."""

    if candidate.relation_type is not RelationType.PREREQUISITE_OF:
        return
    if candidate.from_concept_id == candidate.to_concept_id:
        raise GraphSelfLoopError("A concept cannot be its own prerequisite")

    adjacency: dict[uuid.UUID, set[uuid.UUID]] = {}
    for relation in approved_relations:
        if (
            relation.course_id != candidate.course_id
            or relation.relation_type is not RelationType.PREREQUISITE_OF
        ):
            continue
        adjacency.setdefault(relation.from_concept_id, set()).add(
            relation.to_concept_id
        )

    # Adding A -> B closes a cycle exactly when an approved B -> ... -> A path exists.
    if _is_reachable(
        adjacency,
        start=candidate.to_concept_id,
        target=candidate.from_concept_id,
    ):
        raise GraphCycleError("Approving this prerequisite would create a cycle")


def _is_reachable(
    adjacency: dict[uuid.UUID, set[uuid.UUID]],
    *,
    start: uuid.UUID,
    target: uuid.UUID,
) -> bool:
    pending = [start]
    visited: set[uuid.UUID] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, ()))
    return False


def _review_time(value: datetime | None) -> datetime:
    actual = value or datetime.now(UTC)
    if actual.tzinfo is None:
        raise ValueError("reviewed_at must be timezone-aware")
    return actual.astimezone(UTC)
