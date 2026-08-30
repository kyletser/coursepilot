from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.graph.domain import RelationType, require_uuid, validate_relation_type
from app.graph.errors import InvalidGraphQueryError


@dataclass(frozen=True, slots=True)
class ApprovedGraphQuery:
    """Student-facing graph query contract; review status is intentionally absent."""

    course_id: uuid.UUID | str
    concept_ids: tuple[uuid.UUID | str, ...] = ()
    relation_types: tuple[RelationType | str, ...] = (RelationType.PREREQUISITE_OF,)
    max_depth: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "course_id", require_uuid(self.course_id, "course_id"))
        object.__setattr__(
            self,
            "concept_ids",
            tuple(require_uuid(value, "concept_id") for value in self.concept_ids),
        )
        if not self.relation_types:
            raise InvalidGraphQueryError("At least one relation type is required")
        object.__setattr__(
            self,
            "relation_types",
            tuple(validate_relation_type(value) for value in self.relation_types),
        )
        if isinstance(self.max_depth, bool) or not 1 <= self.max_depth <= 4:
            raise InvalidGraphQueryError("max_depth must be between 1 and 4")

    def compile(self) -> tuple[str, dict[str, object]]:
        relation_types = "|".join(
            item.value for item in dict.fromkeys(self.relation_types)
        )
        # Depth and relationship tokens are validated enums/integers, never raw input.
        cypher = f"""
MATCH path = (source:Concept)-[:{relation_types}*1..{self.max_depth}]->(target:Concept)
WHERE all(relation IN relationships(path) WHERE relation.status = 'APPROVED')
  AND all(concept IN nodes(path) WHERE concept.status = 'APPROVED')
  AND all(concept IN nodes(path) WHERE concept.course_id = $course_id)
  AND (
    size($concept_ids) = 0
    OR source.concept_id IN $concept_ids
    OR target.concept_id IN $concept_ids
  )
RETURN source, relationships(path) AS relations, target
""".strip()
        return cypher, {
            "course_id": str(self.course_id),
            "concept_ids": [str(value) for value in self.concept_ids],
        }
