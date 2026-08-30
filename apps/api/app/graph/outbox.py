from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.graph.domain import ApprovedConcept, ApprovedRelation, ReviewStatus
from app.graph.errors import InvalidOutboxEventError


class GraphEventType(str, enum.Enum):
    CONCEPT_APPROVED = "GRAPH_CONCEPT_APPROVED"
    RELATION_APPROVED = "GRAPH_RELATION_APPROVED"


@dataclass(frozen=True, slots=True)
class GraphOutboxEvent:
    """JSON-ready event written beside the approved PostgreSQL review fact."""

    id: uuid.UUID
    event_type: GraphEventType
    aggregate_id: uuid.UUID
    payload: dict[str, Any]
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.payload.get("fact_source") != "POSTGRESQL":
            raise InvalidOutboxEventError(
                "Graph outbox payload must identify PostgreSQL as its fact source"
            )
        if self.payload.get("status") != ReviewStatus.APPROVED.value:
            raise InvalidOutboxEventError(
                "Only APPROVED graph facts can be synchronized"
            )
        if self.payload.get("schema_version") != 1:
            raise InvalidOutboxEventError("Unsupported graph event schema version")


def concept_approved_event(
    concept: ApprovedConcept, *, event_id: uuid.UUID | None = None
) -> GraphOutboxEvent:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "fact_source": "POSTGRESQL",
        "concept_id": str(concept.id),
        "course_id": str(concept.course_id),
        "name": concept.name,
        "description": concept.description,
        "source_chunk_id": str(concept.source_chunk_id),
        "confidence": concept.confidence,
        "status": concept.status.value,
        "reviewer_id": str(concept.reviewer_id),
        "reviewed_at": _utc_isoformat(concept.reviewed_at),
    }
    return GraphOutboxEvent(
        id=event_id or uuid.uuid4(),
        event_type=GraphEventType.CONCEPT_APPROVED,
        aggregate_id=concept.id,
        payload=payload,
        occurred_at=_to_utc(concept.reviewed_at),
    )


def relation_approved_event(
    relation: ApprovedRelation, *, event_id: uuid.UUID | None = None
) -> GraphOutboxEvent:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "fact_source": "POSTGRESQL",
        "relation_id": str(relation.id),
        "course_id": str(relation.course_id),
        "from_concept_id": str(relation.from_concept_id),
        "to_concept_id": str(relation.to_concept_id),
        "relation_type": relation.relation_type.value,
        "source_chunk_id": str(relation.source_chunk_id),
        "confidence": relation.confidence,
        "status": relation.status.value,
        "reviewer_id": str(relation.reviewer_id),
        "reviewed_at": _utc_isoformat(relation.reviewed_at),
    }
    return GraphOutboxEvent(
        id=event_id or uuid.uuid4(),
        event_type=GraphEventType.RELATION_APPROVED,
        aggregate_id=relation.id,
        payload=payload,
        occurred_at=_to_utc(relation.reviewed_at),
    )


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("reviewed_at must be timezone-aware")
    return value.astimezone(UTC)


def _utc_isoformat(value: datetime) -> str:
    return _to_utc(value).isoformat().replace("+00:00", "Z")
