from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.models import (
    BadCase,
    BadCaseSourceType,
    BadCaseStatus,
    Chunk,
    ConceptCandidate,
    Document,
    DocumentVersion,
    DocumentVersionStatus,
    EvalDataset,
    EvalDatasetStatus,
    EvalDatasetType,
    EvalRun,
    EvalRunStatus,
    MasteryState,
    QuizAttempt,
    QuizAttemptStatus,
    QuizDifficulty,
    QuizItem,
    ReviewStatus,
)
from tests.helpers import auth_headers, create_course, register_and_login


async def _join(client, tokens: dict, invite_code: str) -> None:
    response = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(tokens),
        json={"invite_code": invite_code},
    )
    assert response.status_code == 201, response.text


async def _seed_reviewed_quiz(session, course_id: uuid.UUID, *, suffix: str):
    document = Document(course_id=course_id, logical_name=f"governance-{suffix}")
    session.add(document)
    await session.flush()
    version = DocumentVersion(
        document_id=document.id,
        sha256=(suffix * 64)[:64],
        version=1,
        original_filename=f"governance-{suffix}.md",
        media_type="text/markdown",
        file_path=f"/test/governance-{suffix}.md",
        size_bytes=64,
        status=DocumentVersionStatus.PUBLISHED,
    )
    session.add(version)
    await session.flush()
    chunk = Chunk(
        version_id=version.id,
        ordinal=0,
        content=f"Reviewed governance evidence {suffix}.",
        char_count=32,
    )
    session.add(chunk)
    await session.flush()
    concept = ConceptCandidate(
        course_id=course_id,
        name=f"Concept {suffix}",
        description="Reviewed concept",
        source_chunk_id=chunk.id,
        evidence=chunk.content,
        extraction_model="test",
        confidence=1.0,
        status=ReviewStatus.APPROVED,
    )
    session.add(concept)
    await session.flush()
    quiz = QuizItem(
        course_id=course_id,
        concept_id=concept.id,
        source_chunk_id=chunk.id,
        question=f"Question {suffix}?",
        options=["A", "B", "C"],
        answer="A",
        explanation="Reviewed evidence supports A.",
        difficulty=QuizDifficulty.MEDIUM,
        status=ReviewStatus.APPROVED,
        generation_model="test",
    )
    session.add(quiz)
    await session.flush()
    return concept, quiz


async def test_learning_summary_is_owner_only_course_scoped_and_measured(
    client, app_instance
):
    _, owner_tokens = await register_and_login(
        client, "summary-owner@example.com", role="TEACHER"
    )
    _, other_teacher_tokens = await register_and_login(
        client, "summary-other-teacher@example.com", role="TEACHER"
    )
    student, student_tokens = await register_and_login(
        client, "summary-student@example.com", role="STUDENT"
    )
    unassessed, unassessed_tokens = await register_and_login(
        client, "summary-unassessed@example.com", role="STUDENT"
    )
    course = await create_course(client, owner_tokens, code="SUMMARY-A")
    other_course = await create_course(
        client,
        other_teacher_tokens,
        template="OPERATING_SYSTEMS",
        code="SUMMARY-B",
        name="Other summary course",
    )
    await _join(client, student_tokens, course["invite_code"])
    await _join(client, unassessed_tokens, course["invite_code"])
    await _join(client, student_tokens, other_course["invite_code"])

    assessed_at = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
    async with app_instance.state.session_factory() as session:
        concept, quiz = await _seed_reviewed_quiz(
            session, uuid.UUID(course["id"]), suffix="a"
        )
        other_concept, other_quiz = await _seed_reviewed_quiz(
            session, uuid.UUID(other_course["id"]), suffix="b"
        )
        attempts = [
            QuizAttempt(
                quiz_item_id=quiz.id,
                student_id=uuid.UUID(student["id"]),
                answer="A",
                correct=True,
                idempotency_key="summary-correct",
                weight=1.0,
                status=QuizAttemptStatus.GRADED,
            ),
            QuizAttempt(
                quiz_item_id=quiz.id,
                student_id=uuid.UUID(student["id"]),
                answer="B",
                correct=False,
                idempotency_key="summary-wrong",
                weight=1.0,
                status=QuizAttemptStatus.GRADED,
            ),
            QuizAttempt(
                quiz_item_id=other_quiz.id,
                student_id=uuid.UUID(student["id"]),
                answer="A",
                correct=True,
                idempotency_key="summary-other-course",
                weight=1.0,
                status=QuizAttemptStatus.GRADED,
            ),
        ]
        session.add_all(attempts)
        await session.flush()
        session.add_all(
            [
                MasteryState(
                    course_id=uuid.UUID(course["id"]),
                    student_id=uuid.UUID(student["id"]),
                    concept_id=concept.id,
                    alpha=1.0,
                    beta=3.0,
                    attempt_count=2,
                    last_assessed_at=assessed_at,
                    last_attempt_id=attempts[1].id,
                ),
                MasteryState(
                    course_id=uuid.UUID(other_course["id"]),
                    student_id=uuid.UUID(student["id"]),
                    concept_id=other_concept.id,
                    alpha=9.0,
                    beta=1.0,
                    attempt_count=1,
                    last_assessed_at=assessed_at,
                    last_attempt_id=attempts[2].id,
                ),
            ]
        )
        await session.commit()

    response = await client.get(
        f"/api/v1/courses/{course['id']}/students/learning-summary",
        headers=auth_headers(owner_tokens),
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    by_email = {item["email"]: item for item in data["students"]}
    measured = by_email[student["email"]]
    assert measured["quiz_attempt_count"] == 2
    assert measured["quiz_correct_count"] == 1
    assert measured["mastery_concept_count"] == 1
    assert measured["average_mastery"] == pytest.approx(0.25)
    assert measured["weak_concept_count"] == 1
    assert measured["last_assessed_at"] == assessed_at.isoformat()
    assert measured["enrollment_status"] == "ACTIVE"
    assert by_email[unassessed["email"]]["quiz_attempt_count"] == 0
    assert by_email[unassessed["email"]]["average_mastery"] is None
    assert data["weak_concepts"] == [
        {
            "concept_id": str(concept.id),
            "concept_name": "Concept a",
            "assessed_student_count": 1,
            "weak_student_count": 1,
            "average_mastery": pytest.approx(0.25),
        }
    ]
    assert "Other summary course" not in response.text

    denied = await client.get(
        f"/api/v1/courses/{course['id']}/students/learning-summary",
        headers=auth_headers(other_teacher_tokens),
    )
    assert denied.status_code == 403
    student_denied = await client.get(
        f"/api/v1/courses/{course['id']}/students/learning-summary",
        headers=auth_headers(student_tokens),
    )
    assert student_denied.status_code == 403


async def test_bad_case_list_filters_paginates_and_isolates_courses(
    client, app_instance
):
    owner, owner_tokens = await register_and_login(
        client, "bad-list-owner@example.com", role="TEACHER"
    )
    other_owner, other_tokens = await register_and_login(
        client, "bad-list-other@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "bad-list-student@example.com", role="STUDENT"
    )
    course = await create_course(client, owner_tokens, code="BAD-LIST-A")
    other_course = await create_course(
        client,
        other_tokens,
        template="OPERATING_SYSTEMS",
        code="BAD-LIST-B",
        name="Other bad cases",
    )
    base_time = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    own_ids = [uuid.uuid4(), uuid.uuid4(), uuid.uuid4()]
    async with app_instance.state.session_factory() as session:
        session.add_all(
            [
                BadCase(
                    id=own_ids[0],
                    course_id=uuid.UUID(course["id"]),
                    source_type=BadCaseSourceType.MESSAGE,
                    source_id=uuid.uuid4(),
                    category="GROUNDING",
                    status=BadCaseStatus.OPEN,
                    notes="oldest",
                    reported_by=uuid.UUID(owner["id"]),
                    created_at=base_time,
                ),
                BadCase(
                    id=own_ids[1],
                    course_id=uuid.UUID(course["id"]),
                    source_type=BadCaseSourceType.EVAL_RUN,
                    source_id=uuid.uuid4(),
                    category="RETRIEVAL",
                    status=BadCaseStatus.FIXED,
                    notes="middle",
                    reported_by=uuid.UUID(owner["id"]),
                    created_at=base_time + timedelta(minutes=1),
                ),
                BadCase(
                    id=own_ids[2],
                    course_id=uuid.UUID(course["id"]),
                    source_type=BadCaseSourceType.EVAL_RUN,
                    source_id=uuid.uuid4(),
                    category="RETRIEVAL",
                    status=BadCaseStatus.OPEN,
                    notes="newest",
                    reported_by=uuid.UUID(owner["id"]),
                    created_at=base_time + timedelta(minutes=2),
                ),
                BadCase(
                    course_id=uuid.UUID(other_course["id"]),
                    source_type=BadCaseSourceType.EVAL_RUN,
                    source_id=uuid.uuid4(),
                    category="RETRIEVAL",
                    status=BadCaseStatus.OPEN,
                    notes="SECRET_OTHER_COURSE",
                    reported_by=uuid.UUID(other_owner["id"]),
                    created_at=base_time + timedelta(minutes=3),
                ),
            ]
        )
        await session.commit()

    paged = await client.get(
        f"/api/v1/courses/{course['id']}/bad-cases",
        headers=auth_headers(owner_tokens),
        params={"limit": 1, "offset": 1},
    )
    assert paged.status_code == 200, paged.text
    page = paged.json()["data"]
    assert page["total"] == 3
    assert page["limit"] == 1
    assert page["offset"] == 1
    assert len(page["items"]) == 1
    assert page["items"][0]["id"] == str(own_ids[1])
    assert "SECRET_OTHER_COURSE" not in paged.text

    filtered = await client.get(
        f"/api/v1/courses/{course['id']}/bad-cases",
        headers=auth_headers(owner_tokens),
        params={
            "status": "OPEN",
            "source_type": "EVAL_RUN",
            "category": "RETRIEVAL",
        },
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"]["total"] == 1
    assert filtered.json()["data"]["items"][0]["id"] == str(own_ids[2])

    denied = await client.get(
        f"/api/v1/courses/{course['id']}/bad-cases",
        headers=auth_headers(other_tokens),
    )
    assert denied.status_code == 403
    student_denied = await client.get(
        f"/api/v1/courses/{course['id']}/bad-cases",
        headers=auth_headers(student_tokens),
    )
    assert student_denied.status_code == 403


async def test_eval_run_list_filters_paginates_and_enriches_dataset(
    client, app_instance
):
    owner, owner_tokens = await register_and_login(
        client, "run-list-owner@example.com", role="TEACHER"
    )
    other_owner, other_tokens = await register_and_login(
        client, "run-list-other@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "run-list-student@example.com", role="STUDENT"
    )
    course = await create_course(client, owner_tokens, code="RUN-LIST-A")
    other_course = await create_course(
        client,
        other_tokens,
        template="OPERATING_SYSTEMS",
        code="RUN-LIST-B",
        name="Other runs",
    )
    base_time = datetime(2026, 8, 30, 11, 0, tzinfo=UTC)
    run_ids = [uuid.uuid4(), uuid.uuid4()]
    async with app_instance.state.session_factory() as session:
        dataset = EvalDataset(
            course_id=uuid.UUID(course["id"]),
            name="Routing frozen v2",
            type=EvalDatasetType.INTENT_ROUTING,
            version=2,
            status=EvalDatasetStatus.FROZEN,
            created_by=uuid.UUID(owner["id"]),
            frozen_at=base_time,
        )
        other_dataset = EvalDataset(
            course_id=uuid.UUID(other_course["id"]),
            name="SECRET_OTHER_DATASET",
            type=EvalDatasetType.RETRIEVAL,
            version=1,
            status=EvalDatasetStatus.FROZEN,
            created_by=uuid.UUID(other_owner["id"]),
            frozen_at=base_time,
        )
        session.add_all([dataset, other_dataset])
        await session.flush()
        session.add_all(
            [
                EvalRun(
                    id=run_ids[0],
                    dataset_id=dataset.id,
                    config={"experiment": "a"},
                    status=EvalRunStatus.FAILED,
                    trace_id=uuid.uuid4(),
                    git_commit="a" * 40,
                    index_version=1,
                    created_at=base_time,
                ),
                EvalRun(
                    id=run_ids[1],
                    dataset_id=dataset.id,
                    config={"experiment": "b"},
                    status=EvalRunStatus.SUCCEEDED,
                    metrics={"routing": {"macro_f1": 0.5}},
                    trace_id=uuid.uuid4(),
                    git_commit="b" * 40,
                    index_version=1,
                    created_at=base_time + timedelta(minutes=1),
                ),
                EvalRun(
                    dataset_id=other_dataset.id,
                    config={"secret": True},
                    status=EvalRunStatus.SUCCEEDED,
                    trace_id=uuid.uuid4(),
                    git_commit="c" * 40,
                    index_version=1,
                    created_at=base_time + timedelta(minutes=2),
                ),
            ]
        )
        await session.commit()
        dataset_id = dataset.id

    paged = await client.get(
        f"/api/v1/courses/{course['id']}/eval-runs",
        headers=auth_headers(owner_tokens),
        params={"limit": 1, "offset": 1},
    )
    assert paged.status_code == 200, paged.text
    page = paged.json()["data"]
    assert page["total"] == 2
    assert page["limit"] == 1
    assert page["offset"] == 1
    assert page["items"][0]["id"] == str(run_ids[0])
    assert page["items"][0]["dataset_name"] == "Routing frozen v2"
    assert page["items"][0]["dataset_type"] == "INTENT_ROUTING"
    assert page["items"][0]["dataset_version"] == 2
    assert "SECRET_OTHER_DATASET" not in paged.text

    filtered = await client.get(
        f"/api/v1/courses/{course['id']}/eval-runs",
        headers=auth_headers(owner_tokens),
        params={"dataset_id": str(dataset_id), "status": "SUCCEEDED"},
    )
    assert filtered.status_code == 200
    assert filtered.json()["data"]["total"] == 1
    assert filtered.json()["data"]["items"][0]["id"] == str(run_ids[1])

    denied = await client.get(
        f"/api/v1/courses/{course['id']}/eval-runs",
        headers=auth_headers(other_tokens),
    )
    assert denied.status_code == 403
    student_denied = await client.get(
        f"/api/v1/courses/{course['id']}/eval-runs",
        headers=auth_headers(student_tokens),
    )
    assert student_denied.status_code == 403


async def test_browser_dataset_create_case_freeze_flow(client):
    _, owner_tokens = await register_and_login(
        client, "dataset-browser-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, owner_tokens, code="DATASET-BROWSER")
    created = await client.post(
        f"/api/v1/courses/{course['id']}/eval-datasets",
        headers=auth_headers(owner_tokens),
        json={"name": "Browser retrieval", "type": "RETRIEVAL"},
    )
    assert created.status_code == 201, created.text
    dataset = created.json()["data"]

    added = await client.post(
        f"/api/v1/eval-datasets/{dataset['id']}/cases",
        headers=auth_headers(owner_tokens),
        json={
            "case_key": "browser-case-1",
            "input": {"query": "What is a stack?"},
            "expected": {"relevant_chunk_ids": ["chunk-1"]},
            "labels": {"source": "teacher"},
        },
    )
    assert added.status_code == 201, added.text
    assert added.json()["data"]["case_key"] == "browser-case-1"

    frozen = await client.post(
        f"/api/v1/eval-datasets/{dataset['id']}/freeze",
        headers=auth_headers(owner_tokens),
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["data"]["status"] == "FROZEN"
    assert frozen.json()["data"]["case_count"] == 1
    assert len(frozen.json()["data"]["content_sha256"]) == 64
