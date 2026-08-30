from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from app.graph import (
    ApprovedGraphQuery,
    ApprovedRelation,
    ConceptCandidate,
    GraphCycleError,
    GraphReviewService,
    GraphSelfLoopError,
    InvalidGraphQueryError,
    InvalidRelationTypeError,
    Neo4jGraphConsumer,
    RelationCandidate,
    RelationType,
    SourceChunkRequiredError,
)

COURSE_ID = uuid.uuid4()
REVIEWER_ID = uuid.uuid4()
CHUNK_ID = uuid.uuid4()
REVIEWED_AT = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)


def _relation_candidate(
    source: uuid.UUID,
    target: uuid.UUID,
    relation_type: RelationType | str = RelationType.PREREQUISITE_OF,
    *,
    course_id: uuid.UUID = COURSE_ID,
) -> RelationCandidate:
    return RelationCandidate(
        id=uuid.uuid4(),
        course_id=course_id,
        from_concept_id=source,
        to_concept_id=target,
        relation_type=relation_type,
        source_chunk_id=CHUNK_ID,
        confidence=0.9,
    )


def _approved_relation(
    source: uuid.UUID,
    target: uuid.UUID,
    *,
    course_id: uuid.UUID = COURSE_ID,
    relation_type: RelationType = RelationType.PREREQUISITE_OF,
) -> ApprovedRelation:
    return ApprovedRelation(
        id=uuid.uuid4(),
        course_id=course_id,
        from_concept_id=source,
        to_concept_id=target,
        relation_type=relation_type,
        source_chunk_id=CHUNK_ID,
        confidence=0.9,
        reviewer_id=REVIEWER_ID,
        reviewed_at=REVIEWED_AT,
    )


def test_candidates_require_source_evidence_and_known_relation_types():
    common = {
        "id": uuid.uuid4(),
        "course_id": COURSE_ID,
        "source_chunk_id": None,
        "confidence": 0.8,
    }
    with pytest.raises(SourceChunkRequiredError) as missing_source:
        ConceptCandidate(name="Stack", description="LIFO", **common)
    assert missing_source.value.code == "GRAPH_SOURCE_CHUNK_REQUIRED"

    with pytest.raises(InvalidRelationTypeError):
        _relation_candidate(uuid.uuid4(), uuid.uuid4(), "DEPENDS_ON")


def test_prerequisite_approval_rejects_self_loops_and_course_local_cycles():
    service = GraphReviewService()
    concept_a, concept_b, concept_c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    with pytest.raises(GraphSelfLoopError):
        service.approve_relation(
            _relation_candidate(concept_a, concept_a),
            approved_relations=(),
            reviewer_id=REVIEWER_ID,
        )

    approved = (
        _approved_relation(concept_a, concept_b),
        _approved_relation(concept_b, concept_c),
        # A path in another course must not poison this course's DAG.
        _approved_relation(
            concept_c,
            concept_a,
            course_id=uuid.uuid4(),
        ),
    )
    with pytest.raises(GraphCycleError):
        service.approve_relation(
            _relation_candidate(concept_c, concept_a),
            approved_relations=approved,
            reviewer_id=REVIEWER_ID,
        )

    allowed = service.approve_relation(
        _relation_candidate(concept_c, concept_a, RelationType.RELATED_TO),
        approved_relations=approved,
        reviewer_id=REVIEWER_ID,
        reviewed_at=REVIEWED_AT,
    )
    assert allowed.fact.relation_type is RelationType.RELATED_TO


def test_approval_returns_postgres_fact_outbox_payload():
    source, target = uuid.uuid4(), uuid.uuid4()
    candidate = _relation_candidate(source, target)

    approval = GraphReviewService().approve_relation(
        candidate,
        approved_relations=(),
        reviewer_id=REVIEWER_ID,
        reviewed_at=REVIEWED_AT,
        event_id=uuid.UUID("00000000-0000-0000-0000-000000000123"),
    )

    payload = approval.outbox_event.payload
    assert approval.fact.status.value == "APPROVED"
    assert payload == {
        "schema_version": 1,
        "fact_source": "POSTGRESQL",
        "relation_id": str(candidate.id),
        "course_id": str(COURSE_ID),
        "from_concept_id": str(source),
        "to_concept_id": str(target),
        "relation_type": "PREREQUISITE_OF",
        "source_chunk_id": str(CHUNK_ID),
        "confidence": 0.9,
        "status": "APPROVED",
        "reviewer_id": str(REVIEWER_ID),
        "reviewed_at": "2026-08-30T08:00:00Z",
    }


class _FakeResult:
    def consume(self) -> None:
        return None


class _FakeTransaction:
    def __init__(self, writes: list[tuple[str, dict[str, object]]]) -> None:
        self.writes = writes

    def run(self, query: str, **parameters: object) -> _FakeResult:
        self.writes.append((query, parameters))
        return _FakeResult()


class _FakeSession:
    def __init__(self, writes: list[tuple[str, dict[str, object]]]) -> None:
        self.tx = _FakeTransaction(writes)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute_write(self, callback, event) -> None:
        callback(self.tx, event)


class _FakeDriver:
    def __init__(self) -> None:
        self.writes: list[tuple[str, dict[str, object]]] = []

    def session(self, **_kwargs) -> _FakeSession:
        return _FakeSession(self.writes)


def test_neo4j_consumer_is_driver_injected_and_replay_safe_by_merge():
    service = GraphReviewService()
    concept_approval = service.approve_concept(
        ConceptCandidate(
            id=uuid.uuid4(),
            course_id=COURSE_ID,
            name="Queue",
            description="FIFO",
            source_chunk_id=CHUNK_ID,
            confidence=0.95,
        ),
        reviewer_id=REVIEWER_ID,
        reviewed_at=REVIEWED_AT,
    )
    relation_approval = service.approve_relation(
        _relation_candidate(uuid.uuid4(), uuid.uuid4()),
        approved_relations=(),
        reviewer_id=REVIEWER_ID,
        reviewed_at=REVIEWED_AT,
    )
    driver = _FakeDriver()
    consumer = Neo4jGraphConsumer(driver)

    consumer.consume(concept_approval.outbox_event)
    consumer.consume(relation_approval.outbox_event)
    consumer.consume(relation_approval.outbox_event)

    assert len(driver.writes) == 3
    concept_query, _ = driver.writes[0]
    first_query, first_parameters = driver.writes[1]
    assert "MERGE (concept:Concept {concept_id: $concept_id})" in concept_query
    assert first_query == driver.writes[2][0]
    assert first_parameters == driver.writes[2][1]
    assert "MERGE (source)-[relation:PREREQUISITE_OF" in first_query
    assert "CREATE" not in first_query
    assert first_parameters["relation_id"] == str(relation_approval.fact.id)


def test_query_contract_cannot_request_unapproved_relationships():
    concept_id = uuid.uuid4()
    query, parameters = ApprovedGraphQuery(
        course_id=COURSE_ID,
        concept_ids=(concept_id,),
        relation_types=(RelationType.PREREQUISITE_OF, RelationType.RELATED_TO),
        max_depth=4,
    ).compile()

    assert "relation.status = 'APPROVED'" in query
    assert "concept.status = 'APPROVED'" in query
    assert "PREREQUISITE_OF|RELATED_TO*1..4" in query
    assert "status" not in parameters
    assert parameters == {
        "course_id": str(COURSE_ID),
        "concept_ids": [str(concept_id)],
    }

    with pytest.raises(InvalidGraphQueryError):
        ApprovedGraphQuery(course_id=COURSE_ID, max_depth=5)
    with pytest.raises(TypeError):
        ApprovedGraphQuery(course_id=COURSE_ID, review_status="PENDING")  # type: ignore[call-arg]
