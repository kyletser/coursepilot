from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from app.graph.domain import validate_relation_type
from app.graph.errors import InvalidOutboxEventError
from app.graph.outbox import GraphEventType, GraphOutboxEvent

CONCEPT_MERGE_CYPHER = """
MERGE (concept:Concept {concept_id: $concept_id})
SET concept.course_id = $course_id,
    concept.name = $name,
    concept.description = $description,
    concept.source_chunk_id = $source_chunk_id,
    concept.confidence = $confidence,
    concept.status = 'APPROVED',
    concept.reviewer_id = $reviewer_id,
    concept.reviewed_at = datetime($reviewed_at),
    concept.last_event_id = $event_id
""".strip()


RELATION_MERGE_CYPHER: dict[str, str] = {
    relation_type: f"""
MATCH (source:Concept {{concept_id: $from_concept_id, status: 'APPROVED'}})
MATCH (target:Concept {{concept_id: $to_concept_id, status: 'APPROVED'}})
MERGE (source)-[relation:{relation_type} {{relation_id: $relation_id}}]->(target)
SET relation.course_id = $course_id,
    relation.source_chunk_id = $source_chunk_id,
    relation.confidence = $confidence,
    relation.status = 'APPROVED',
    relation.reviewer_id = $reviewer_id,
    relation.reviewed_at = datetime($reviewed_at),
    relation.last_event_id = $event_id
""".strip()
    for relation_type in (
        "PREREQUISITE_OF",
        "RELATED_TO",
        "PART_OF",
        "CONTRASTS_WITH",
    )
}


class Neo4jTransaction(Protocol):
    def run(self, query: str, **parameters: Any) -> Any: ...


class Neo4jGraphConsumer:
    """Consume approved outbox facts using replay-safe Neo4j MERGE statements."""

    def __init__(self, driver: Any, *, database: str | None = None) -> None:
        self._driver = driver
        self._database = database

    def consume(self, event: GraphOutboxEvent) -> None:
        _validate_approved_payload(event.payload)
        session_kwargs = {"database": self._database} if self._database else {}
        with self._driver.session(**session_kwargs) as session:
            if event.event_type is GraphEventType.CONCEPT_APPROVED:
                session.execute_write(self._merge_concept, event)
                return
            if event.event_type is GraphEventType.RELATION_APPROVED:
                session.execute_write(self._merge_relation, event)
                return
            raise InvalidOutboxEventError(
                f"Unsupported graph event type: {event.event_type}"
            )

    @staticmethod
    def _merge_concept(tx: Neo4jTransaction, event: GraphOutboxEvent) -> None:
        parameters = dict(event.payload)
        parameters["event_id"] = str(event.id)
        result = tx.run(CONCEPT_MERGE_CYPHER, **parameters)
        _consume_result(result)

    @staticmethod
    def _merge_relation(tx: Neo4jTransaction, event: GraphOutboxEvent) -> None:
        relation_type = validate_relation_type(
            event.payload.get("relation_type")  # type: ignore[arg-type]
        ).value
        parameters = dict(event.payload)
        parameters["event_id"] = str(event.id)
        result = tx.run(RELATION_MERGE_CYPHER[relation_type], **parameters)
        _consume_result(result)


def _validate_approved_payload(payload: Mapping[str, Any]) -> None:
    if payload.get("fact_source") != "POSTGRESQL":
        raise InvalidOutboxEventError(
            "Neo4j only consumes graph facts sourced from PostgreSQL"
        )
    if payload.get("status") != "APPROVED":
        raise InvalidOutboxEventError("Neo4j only consumes APPROVED graph facts")


def _consume_result(result: Any) -> None:
    consume = getattr(result, "consume", None)
    if consume is not None:
        consume()
