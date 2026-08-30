from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import func, select

from app.models import (
    Chunk,
    ConceptCandidate,
    CourseIndex,
    CourseIndexStatus,
    Document,
    DocumentVersion,
    DocumentVersionStatus,
    GraphOutbox,
    GraphOutboxStatus,
    IndexComponentStatus,
    IngestionJob,
    IngestionStage,
    RelationCandidate,
    RelationType,
    ReviewStatus,
)
from app.routers.graph import router as graph_router
from app.tasks.graph import deliver_graph_outbox_event
from tests.helpers import (
    auth_headers,
    create_course,
    register_and_login,
)


@pytest_asyncio.fixture(autouse=True)
async def install_graph_routes(app_instance):
    if not any(
        getattr(route, "path", None) == "/api/v1/courses/{course_id}/graph"
        for route in app_instance.routes
    ):
        app_instance.include_router(graph_router)


async def _seed_material(
    app_instance,
    course_id: uuid.UUID,
    *,
    logical_name: str = "chapter-one",
):
    async with app_instance.state.session_factory() as session:
        document = Document(course_id=course_id, logical_name=logical_name)
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            document_id=document.id,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            version=1,
            original_filename=f"{logical_name}.md",
            media_type="text/markdown",
            file_path=f"/tmp/{uuid.uuid4()}.md",
            size_bytes=64,
            status=DocumentVersionStatus.READY_FOR_REVIEW,
        )
        session.add(version)
        await session.flush()
        chunk = Chunk(
            version_id=version.id,
            ordinal=0,
            content="Stacks are LIFO; queues are FIFO.",
            page=1,
            section_path=["Linear structures"],
            char_count=34,
        )
        session.add(chunk)
        await session.flush()
        await session.commit()
        return document.id, version.id, chunk.id


async def _add_concept(
    app_instance,
    course_id: uuid.UUID,
    chunk_id: uuid.UUID,
    name: str,
    *,
    status: ReviewStatus = ReviewStatus.PENDING,
    index_id: uuid.UUID | None = None,
) -> uuid.UUID:
    async with app_instance.state.session_factory() as session:
        candidate = ConceptCandidate(
            course_id=course_id,
            index_id=index_id,
            name=name,
            description=f"{name} description",
            aliases=[],
            source_chunk_id=chunk_id,
            evidence=f"Evidence for {name}",
            extraction_model="test-model",
            confidence=0.9,
            status=status,
        )
        session.add(candidate)
        await session.commit()
        return candidate.id


async def _add_relation(
    app_instance,
    course_id: uuid.UUID,
    chunk_id: uuid.UUID,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
    *,
    status: ReviewStatus = ReviewStatus.PENDING,
) -> uuid.UUID:
    async with app_instance.state.session_factory() as session:
        candidate = RelationCandidate(
            course_id=course_id,
            from_candidate_id=source_id,
            to_candidate_id=target_id,
            type=RelationType.PREREQUISITE_OF,
            source_chunk_id=chunk_id,
            evidence="Prerequisite evidence",
            extraction_model="test-model",
            confidence=0.9,
            status=status,
        )
        session.add(candidate)
        await session.commit()
        return candidate.id


async def test_graph_permissions_and_student_view_never_leaks_pending(
    client, app_instance
):
    teacher, teacher_tokens = await register_and_login(
        client, "graph-owner@example.com", role="TEACHER"
    )
    _, other_teacher_tokens = await register_and_login(
        client, "graph-other@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "graph-student@example.com", role="STUDENT"
    )
    course = await create_course(client, teacher_tokens)
    course_id = uuid.UUID(course["id"])
    await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    _, _, chunk_id = await _seed_material(app_instance, course_id)
    stack_id = await _add_concept(
        app_instance, course_id, chunk_id, "Stack", status=ReviewStatus.APPROVED
    )
    queue_id = await _add_concept(
        app_instance, course_id, chunk_id, "Queue", status=ReviewStatus.APPROVED
    )
    pending_id = await _add_concept(app_instance, course_id, chunk_id, "Pending secret")
    approved_relation_id = await _add_relation(
        app_instance,
        course_id,
        chunk_id,
        stack_id,
        queue_id,
        status=ReviewStatus.APPROVED,
    )
    await _add_relation(app_instance, course_id, chunk_id, queue_id, pending_id)

    owner_list = await client.get(
        f"/api/v1/courses/{course_id}/graph/candidates",
        headers=auth_headers(teacher_tokens),
    )
    assert owner_list.status_code == 200
    assert len(owner_list.json()["data"]["concepts"]) == 3
    assert owner_list.json()["data"]["concepts"][0]["source"]["content"]

    student_review = await client.get(
        f"/api/v1/courses/{course_id}/graph/candidates",
        headers=auth_headers(student_tokens),
    )
    assert student_review.status_code == 403
    other_review = await client.get(
        f"/api/v1/courses/{course_id}/graph/candidates",
        headers=auth_headers(other_teacher_tokens),
    )
    assert other_review.status_code == 403

    graph = await client.get(
        f"/api/v1/courses/{course_id}/graph",
        headers=auth_headers(student_tokens),
    )
    assert graph.status_code == 200
    data = graph.json()["data"]
    assert {item["id"] for item in data["concepts"]} == {
        str(stack_id),
        str(queue_id),
    }
    assert [item["id"] for item in data["relations"]] == [str(approved_relation_id)]
    assert "Pending secret" not in graph.text
    assert str(pending_id) not in graph.text
    assert teacher["id"] not in graph.text


class _RecordingConsumer:
    def __init__(self) -> None:
        self.events = []

    def consume(self, event) -> None:
        self.events.append(event)


async def test_approvals_write_outbox_reject_cross_course_source_and_block_cycle(
    client, app_instance
):
    _, teacher_tokens = await register_and_login(
        client, "review-owner@example.com", role="TEACHER"
    )
    _, other_tokens = await register_and_login(
        client, "source-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, teacher_tokens)
    other_course = await create_course(
        client, other_tokens, code="CS202", template="OPERATING_SYSTEMS"
    )
    course_id = uuid.UUID(course["id"])
    _, _, chunk_id = await _seed_material(app_instance, course_id)
    _, _, other_chunk_id = await _seed_material(
        app_instance, uuid.UUID(other_course["id"]), logical_name="foreign"
    )
    concept_ids = [
        await _add_concept(app_instance, course_id, chunk_id, name)
        for name in ("A", "B", "C")
    ]
    foreign_source_id = await _add_concept(
        app_instance, course_id, other_chunk_id, "Invalid source"
    )

    edited = await client.patch(
        f"/api/v1/graph/concepts/{concept_ids[0]}",
        headers=auth_headers(teacher_tokens),
        json={"name": "A edited", "aliases": ["a", "A"]},
    )
    assert edited.status_code == 200
    assert edited.json()["data"]["aliases"] == ["a"]

    for concept_id in concept_ids:
        response = await client.post(
            f"/api/v1/graph/concepts/{concept_id}/approve",
            headers=auth_headers(teacher_tokens),
        )
        assert response.status_code == 200, response.text

    invalid_source = await client.post(
        f"/api/v1/graph/concepts/{foreign_source_id}/approve",
        headers=auth_headers(teacher_tokens),
    )
    assert invalid_source.status_code == 409
    assert invalid_source.json()["error"]["code"] == "GRAPH_SOURCE_COURSE_MISMATCH"

    first = await _add_relation(
        app_instance, course_id, chunk_id, concept_ids[0], concept_ids[1]
    )
    second = await _add_relation(
        app_instance, course_id, chunk_id, concept_ids[1], concept_ids[2]
    )
    closes_cycle = await _add_relation(
        app_instance, course_id, chunk_id, concept_ids[2], concept_ids[0]
    )
    for relation_id in (first, second):
        response = await client.post(
            f"/api/v1/graph/relations/{relation_id}/approve",
            headers=auth_headers(teacher_tokens),
        )
        assert response.status_code == 200, response.text
    rejected_cycle = await client.post(
        f"/api/v1/graph/relations/{closes_cycle}/approve",
        headers=auth_headers(teacher_tokens),
    )
    assert rejected_cycle.status_code == 409
    assert rejected_cycle.json()["error"]["code"] == "GRAPH_PREREQUISITE_CYCLE"

    async with app_instance.state.session_factory() as session:
        outbox_count = await session.scalar(
            select(func.count()).select_from(GraphOutbox)
        )
        cycle_status = await session.scalar(
            select(RelationCandidate.status).where(RelationCandidate.id == closes_cycle)
        )
        first_outbox = await session.scalar(
            select(GraphOutbox).where(GraphOutbox.aggregate_id == concept_ids[0])
        )
    assert outbox_count == 5
    assert cycle_status == ReviewStatus.PENDING
    assert first_outbox is not None

    consumer = _RecordingConsumer()
    result = await deliver_graph_outbox_event(
        first_outbox.id,
        session_factory=app_instance.state.session_factory,
        consumer=consumer,
        settings=app_instance.state.settings,
    )
    assert result["status"] == "SUCCEEDED"
    assert consumer.events[0].id == first_outbox.id
    async with app_instance.state.session_factory() as session:
        assert (
            await session.scalar(
                select(GraphOutbox.status).where(GraphOutbox.id == first_outbox.id)
            )
            == GraphOutboxStatus.SUCCEEDED
        )


async def test_publish_is_guarded_and_switches_index_and_ingestion_atomically(
    client, app_instance
):
    _, teacher_tokens = await register_and_login(
        client, "publish-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, teacher_tokens)
    course_id = uuid.UUID(course["id"])
    _, version_id, chunk_id = await _seed_material(app_instance, course_id)

    async with app_instance.state.session_factory() as session:
        previous = CourseIndex(
            course_id=course_id,
            version=1,
            dense_status=IndexComponentStatus.READY,
            lexical_status=IndexComponentStatus.READY,
            status=CourseIndexStatus.ACTIVE,
        )
        target = CourseIndex(
            course_id=course_id,
            version=2,
            dense_status=IndexComponentStatus.READY,
            lexical_status=IndexComponentStatus.READY,
            status=CourseIndexStatus.READY,
        )
        job = IngestionJob(
            version_id=version_id,
            stage=IngestionStage.READY_FOR_REVIEW,
            progress=100,
        )
        session.add_all((previous, target, job))
        await session.flush()
        pending = ConceptCandidate(
            course_id=course_id,
            index_id=target.id,
            name="Reviewed before publish",
            description="",
            aliases=[],
            source_chunk_id=chunk_id,
            evidence="evidence",
            extraction_model="test-model",
            confidence=0.8,
        )
        session.add(pending)
        await session.commit()
        previous_id, target_id, pending_id = previous.id, target.id, pending.id

    blocked = await client.post(
        f"/api/v1/courses/{course_id}/graph/publish",
        headers=auth_headers(teacher_tokens),
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "GRAPH_REVIEW_PENDING"

    rejected = await client.post(
        f"/api/v1/graph/concepts/{pending_id}/reject",
        headers=auth_headers(teacher_tokens),
    )
    assert rejected.status_code == 200
    published = await client.post(
        f"/api/v1/courses/{course_id}/graph/publish",
        headers=auth_headers(teacher_tokens),
    )
    assert published.status_code == 200, published.text
    assert published.json()["data"]["index_id"] == str(target_id)
    assert published.json()["data"]["status"] == "ACTIVE"

    async with app_instance.state.session_factory() as session:
        previous_status = await session.scalar(
            select(CourseIndex.status).where(CourseIndex.id == previous_id)
        )
        target_status = await session.scalar(
            select(CourseIndex.status).where(CourseIndex.id == target_id)
        )
        version_status = await session.scalar(
            select(DocumentVersion.status).where(DocumentVersion.id == version_id)
        )
        job_stage = await session.scalar(
            select(IngestionJob.stage).where(IngestionJob.version_id == version_id)
        )
    assert previous_status == CourseIndexStatus.ARCHIVED
    assert target_status == CourseIndexStatus.ACTIVE
    assert version_status == DocumentVersionStatus.PUBLISHED
    assert job_stage == IngestionStage.PUBLISHED
