from __future__ import annotations

import enum
import math
import uuid
from dataclasses import dataclass
from datetime import datetime

from app.graph.errors import (
    InvalidRelationTypeError,
    SourceChunkRequiredError,
)


class RelationType(str, enum.Enum):
    PREREQUISITE_OF = "PREREQUISITE_OF"
    RELATED_TO = "RELATED_TO"
    PART_OF = "PART_OF"
    CONTRASTS_WITH = "CONTRASTS_WITH"


class ReviewStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


def require_uuid(value: uuid.UUID | str | None, field_name: str) -> uuid.UUID:
    if value is None or (isinstance(value, str) and not value.strip()):
        if field_name == "source_chunk_id":
            raise SourceChunkRequiredError("Graph candidates require a source_chunk_id")
        raise ValueError(f"{field_name} is required")
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a UUID") from exc


def require_source_chunk(value: uuid.UUID | str | None) -> uuid.UUID:
    try:
        return require_uuid(value, "source_chunk_id")
    except ValueError as exc:
        if isinstance(exc, SourceChunkRequiredError):
            raise
        raise SourceChunkRequiredError(
            "Graph candidates require a valid source_chunk_id"
        ) from exc


def validate_relation_type(value: RelationType | str) -> RelationType:
    try:
        return value if isinstance(value, RelationType) else RelationType(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in RelationType)
        raise InvalidRelationTypeError(
            f"Unsupported graph relation type; expected one of: {allowed}"
        ) from exc


def _validate_confidence(value: float) -> float:
    numeric = float(value)
    if not math.isfinite(numeric) or not 0 <= numeric <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return numeric


@dataclass(frozen=True, slots=True)
class ConceptCandidate:
    id: uuid.UUID | str
    course_id: uuid.UUID | str
    name: str
    description: str
    source_chunk_id: uuid.UUID | str | None
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_uuid(self.id, "id"))
        object.__setattr__(self, "course_id", require_uuid(self.course_id, "course_id"))
        object.__setattr__(
            self, "source_chunk_id", require_source_chunk(self.source_chunk_id)
        )
        name = self.name.strip()
        if not name:
            raise ValueError("name is required")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "description", self.description.strip())
        object.__setattr__(self, "confidence", _validate_confidence(self.confidence))


@dataclass(frozen=True, slots=True)
class RelationCandidate:
    id: uuid.UUID | str
    course_id: uuid.UUID | str
    from_concept_id: uuid.UUID | str
    to_concept_id: uuid.UUID | str
    relation_type: RelationType | str
    source_chunk_id: uuid.UUID | str | None
    confidence: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_uuid(self.id, "id"))
        object.__setattr__(self, "course_id", require_uuid(self.course_id, "course_id"))
        object.__setattr__(
            self,
            "from_concept_id",
            require_uuid(self.from_concept_id, "from_concept_id"),
        )
        object.__setattr__(
            self,
            "to_concept_id",
            require_uuid(self.to_concept_id, "to_concept_id"),
        )
        object.__setattr__(
            self, "relation_type", validate_relation_type(self.relation_type)
        )
        object.__setattr__(
            self, "source_chunk_id", require_source_chunk(self.source_chunk_id)
        )
        object.__setattr__(self, "confidence", _validate_confidence(self.confidence))


@dataclass(frozen=True, slots=True)
class ApprovedConcept:
    id: uuid.UUID | str
    course_id: uuid.UUID | str
    name: str
    description: str
    source_chunk_id: uuid.UUID | str
    confidence: float
    reviewer_id: uuid.UUID | str
    reviewed_at: datetime
    status: ReviewStatus = ReviewStatus.APPROVED

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_uuid(self.id, "id"))
        object.__setattr__(self, "course_id", require_uuid(self.course_id, "course_id"))
        object.__setattr__(
            self, "source_chunk_id", require_source_chunk(self.source_chunk_id)
        )
        object.__setattr__(
            self, "reviewer_id", require_uuid(self.reviewer_id, "reviewer_id")
        )
        if self.status is not ReviewStatus.APPROVED:
            raise ValueError("ApprovedConcept status must be APPROVED")


@dataclass(frozen=True, slots=True)
class ApprovedRelation:
    id: uuid.UUID | str
    course_id: uuid.UUID | str
    from_concept_id: uuid.UUID | str
    to_concept_id: uuid.UUID | str
    relation_type: RelationType | str
    source_chunk_id: uuid.UUID | str
    confidence: float
    reviewer_id: uuid.UUID | str
    reviewed_at: datetime
    status: ReviewStatus = ReviewStatus.APPROVED

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", require_uuid(self.id, "id"))
        object.__setattr__(self, "course_id", require_uuid(self.course_id, "course_id"))
        object.__setattr__(
            self,
            "from_concept_id",
            require_uuid(self.from_concept_id, "from_concept_id"),
        )
        object.__setattr__(
            self,
            "to_concept_id",
            require_uuid(self.to_concept_id, "to_concept_id"),
        )
        object.__setattr__(
            self, "relation_type", validate_relation_type(self.relation_type)
        )
        object.__setattr__(
            self, "source_chunk_id", require_source_chunk(self.source_chunk_id)
        )
        object.__setattr__(
            self, "reviewer_id", require_uuid(self.reviewer_id, "reviewer_id")
        )
        if self.status is not ReviewStatus.APPROVED:
            raise ValueError("ApprovedRelation status must be APPROVED")
