from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import httpx
import pytest
import pytest_asyncio

from app.models import (
    Chunk,
    ConceptCandidate,
    Document,
    DocumentVersion,
    DocumentVersionStatus,
    RelationCandidate,
    RelationType,
    ReviewStatus,
)
from app.routers.learning import router as learning_router
from tests.helpers import auth_headers, create_course, register_and_login


@pytest_asyncio.fixture
async def learning_client(app_instance) -> AsyncIterator[httpx.AsyncClient]:
    route_path = "/api/v1/courses/{course_id}/quizzes/generate-candidates"
    if not any(
        getattr(route, "path", None) == route_path for route in app_instance.routes
    ):
        app_instance.include_router(learning_router)
    transport = httpx.ASGITransport(app=app_instance, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        yield client


async def _course_with_enrollment(client: httpx.AsyncClient) -> tuple[dict, dict, dict]:
    _, teacher_tokens = await register_and_login(
        client, "learning-teacher@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "learning-student@example.com", role="STUDENT"
    )
    course = await create_course(client, teacher_tokens)
    joined = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    assert joined.status_code == 201, joined.text
    return teacher_tokens, student_tokens, course


async def _seed_material_and_concepts(
    app_instance,
    course_id: str,
    *,
    statuses: tuple[ReviewStatus, ...] = (ReviewStatus.APPROVED,),
) -> tuple[uuid.UUID, tuple[uuid.UUID, ...]]:
    chunk_id = uuid.uuid4()
    concept_ids = tuple(uuid.uuid4() for _ in statuses)
    async with app_instance.state.session_factory() as session:
        document = Document(course_id=uuid.UUID(course_id), logical_name="quiz-source")
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            document_id=document.id,
            sha256="a" * 64,
            version=1,
            original_filename="quiz.md",
            media_type="text/markdown",
            file_path="/test/quiz.md",
            size_bytes=100,
            status=DocumentVersionStatus.PUBLISHED,
            published_at=datetime.now(UTC),
        )
        session.add(version)
        await session.flush()
        chunk = Chunk(
            id=chunk_id,
            version_id=version.id,
            ordinal=0,
            content="A stack uses last-in, first-out ordering.",
            char_count=42,
        )
        session.add(chunk)
        await session.flush()
        session.add_all(
            [
                ConceptCandidate(
                    id=concept_id,
                    course_id=uuid.UUID(course_id),
                    name=f"Concept {index}",
                    description="Grounded concept",
                    source_chunk_id=chunk_id,
                    evidence=chunk.content,
                    extraction_model="test",
                    confidence=0.95,
                    status=status,
                )
                for index, (concept_id, status) in enumerate(
                    zip(concept_ids, statuses, strict=True)
                )
            ]
        )
        await session.commit()
    return chunk_id, concept_ids


async def _create_candidate(
    client: httpx.AsyncClient,
    teacher_tokens: dict,
    course_id: str,
    chunk_id: uuid.UUID,
    concept_id: uuid.UUID,
) -> dict:
    response = await client.post(
        f"/api/v1/courses/{course_id}/quizzes/generate-candidates",
        headers=auth_headers(teacher_tokens),
        json={
            "items": [
                {
                    "concept_id": str(concept_id),
                    "source_chunk_id": str(chunk_id),
                    "question": "Which ordering does a stack use?",
                    "options": ["FIFO", "LIFO", "Random"],
                    "answer": "LIFO",
                    "explanation": "The source states last-in, first-out.",
                    "difficulty": "MEDIUM",
                }
            ]
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"][0]


@pytest.mark.asyncio
async def test_empty_body_generates_pending_candidates_from_approved_evidence(
    learning_client: httpx.AsyncClient, app_instance
):
    teacher, _, course = await _course_with_enrollment(learning_client)
    chunk_id, (concept_id,) = await _seed_material_and_concepts(
        app_instance, course["id"]
    )

    response = await learning_client.post(
        f"/api/v1/courses/{course['id']}/quizzes/generate-candidates",
        headers=auth_headers(teacher),
    )

    assert response.status_code == 201, response.text
    [candidate] = response.json()["data"]
    assert candidate["concept_id"] == str(concept_id)
    assert candidate["source_chunk_id"] == str(chunk_id)
    assert candidate["status"] == "PENDING"
    assert candidate["generation_model"] == "SOURCE_GROUNDED_DETERMINISTIC_V1"
    assert candidate["answer"] in candidate["options"]
    assert "A stack uses last-in, first-out ordering." in candidate["answer"]


@pytest.mark.asyncio
async def test_empty_body_refuses_generation_without_approved_evidence(
    learning_client: httpx.AsyncClient, app_instance
):
    teacher, _, course = await _course_with_enrollment(learning_client)
    await _seed_material_and_concepts(
        app_instance,
        course["id"],
        statuses=(ReviewStatus.PENDING,),
    )

    response = await learning_client.post(
        f"/api/v1/courses/{course['id']}/quizzes/generate-candidates",
        headers=auth_headers(teacher),
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == ("QUIZ_GENERATION_EVIDENCE_INSUFFICIENT")


@pytest.mark.asyncio
async def test_unreviewed_quiz_never_reaches_student(
    learning_client: httpx.AsyncClient, app_instance
):
    teacher, student, course = await _course_with_enrollment(learning_client)
    chunk_id, (concept_id,) = await _seed_material_and_concepts(
        app_instance, course["id"]
    )
    quiz = await _create_candidate(
        learning_client, teacher, course["id"], chunk_id, concept_id
    )

    hidden = await learning_client.get(
        f"/api/v1/courses/{course['id']}/quizzes/next",
        headers=auth_headers(student),
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "APPROVED_QUIZ_NOT_AVAILABLE"

    approved = await learning_client.post(
        f"/api/v1/quizzes/{quiz['id']}/approve",
        headers=auth_headers(teacher),
    )
    assert approved.status_code == 200, approved.text
    visible = await learning_client.get(
        f"/api/v1/courses/{course['id']}/quizzes/next",
        headers=auth_headers(student),
    )
    assert visible.status_code == 200, visible.text
    assert visible.json()["data"]["id"] == quiz["id"]
    assert "answer" not in visible.json()["data"]


@pytest.mark.asyncio
async def test_duplicate_attempt_replays_without_updating_mastery_twice(
    learning_client: httpx.AsyncClient, app_instance
):
    teacher, student, course = await _course_with_enrollment(learning_client)
    chunk_id, (concept_id,) = await _seed_material_and_concepts(
        app_instance, course["id"]
    )
    quiz = await _create_candidate(
        learning_client, teacher, course["id"], chunk_id, concept_id
    )
    await learning_client.post(
        f"/api/v1/quizzes/{quiz['id']}/approve",
        headers=auth_headers(teacher),
    )
    payload = {"answer": "LIFO", "idempotency_key": "stable-attempt-key"}
    first = await learning_client.post(
        f"/api/v1/quizzes/{quiz['id']}/attempts",
        headers=auth_headers(student),
        json=payload,
    )
    replay = await learning_client.post(
        f"/api/v1/quizzes/{quiz['id']}/attempts",
        headers=auth_headers(student),
        json={**payload, "answer": "FIFO"},
    )
    assert first.status_code == replay.status_code == 200
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]
    assert replay.json()["data"]["correct"] is True
    assert replay.json()["data"]["replayed"] is True

    mastery = await learning_client.get(
        f"/api/v1/courses/{course['id']}/mastery",
        headers=auth_headers(student),
    )
    assert mastery.status_code == 200
    assert mastery.json()["data"][0]["attempt_count"] == 1
    assert mastery.json()["data"][0]["alpha"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_learning_path_uses_only_approved_prerequisites_and_rejects_cycles(
    learning_client: httpx.AsyncClient, app_instance
):
    _, student, course = await _course_with_enrollment(learning_client)
    chunk_id, concept_ids = await _seed_material_and_concepts(
        app_instance,
        course["id"],
        statuses=(
            ReviewStatus.APPROVED,
            ReviewStatus.APPROVED,
            ReviewStatus.APPROVED,
        ),
    )
    prerequisite_id, target_id, pending_prerequisite_id = concept_ids
    async with app_instance.state.session_factory() as session:
        session.add_all(
            [
                RelationCandidate(
                    course_id=uuid.UUID(course["id"]),
                    from_candidate_id=prerequisite_id,
                    to_candidate_id=target_id,
                    type=RelationType.PREREQUISITE_OF,
                    source_chunk_id=chunk_id,
                    evidence="approved edge",
                    extraction_model="test",
                    confidence=0.9,
                    status=ReviewStatus.APPROVED,
                ),
                RelationCandidate(
                    course_id=uuid.UUID(course["id"]),
                    from_candidate_id=pending_prerequisite_id,
                    to_candidate_id=target_id,
                    type=RelationType.PREREQUISITE_OF,
                    source_chunk_id=chunk_id,
                    evidence="pending edge",
                    extraction_model="test",
                    confidence=0.9,
                    status=ReviewStatus.PENDING,
                ),
            ]
        )
        await session.commit()

    path = await learning_client.post(
        f"/api/v1/courses/{course['id']}/learning-path",
        headers=auth_headers(student),
        json={"target_concept_id": str(target_id), "max_depth": 4},
    )
    assert path.status_code == 200, path.text
    assert [step["concept_id"] for step in path.json()["data"]["steps"]] == [
        str(prerequisite_id),
        str(target_id),
    ]

    async with app_instance.state.session_factory() as session:
        session.add(
            RelationCandidate(
                course_id=uuid.UUID(course["id"]),
                from_candidate_id=target_id,
                to_candidate_id=prerequisite_id,
                type=RelationType.PREREQUISITE_OF,
                source_chunk_id=chunk_id,
                evidence="cycle",
                extraction_model="test",
                confidence=0.9,
                status=ReviewStatus.APPROVED,
            )
        )
        await session.commit()
    cyclic = await learning_client.post(
        f"/api/v1/courses/{course['id']}/learning-path",
        headers=auth_headers(student),
        json={"target_concept_id": str(target_id)},
    )
    assert cyclic.status_code == 409
    assert cyclic.json()["error"]["code"] == "PREREQUISITE_CYCLE_DETECTED"
