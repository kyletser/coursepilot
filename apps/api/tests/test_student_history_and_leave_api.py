from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.models import (
    ChatSession,
    Chunk,
    ConceptCandidate,
    Document,
    DocumentVersion,
    DocumentVersionStatus,
    Enrollment,
    EnrollmentStatus,
    Message,
    MessageRole,
    MessageStatus,
    QuizAttempt,
    QuizAttemptStatus,
    QuizDifficulty,
    QuizItem,
    ReviewStatus,
)
from tests.helpers import auth_headers, create_course, register_and_login


async def _join_course(client, student_tokens: dict, invite_code: str) -> None:
    response = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": invite_code},
    )
    assert response.status_code == 201, response.text


async def _seed_course_material(session, course_id: uuid.UUID, *, suffix: str):
    document = Document(
        course_id=course_id,
        logical_name=f"history-source-{suffix}",
    )
    session.add(document)
    await session.flush()

    version = DocumentVersion(
        document_id=document.id,
        sha256=(suffix * 64)[:64],
        version=1,
        original_filename=f"history-{suffix}.md",
        media_type="text/markdown",
        file_path=f"/test/history-{suffix}.md",
        size_bytes=128,
        status=DocumentVersionStatus.PUBLISHED,
    )
    session.add(version)
    await session.flush()

    chunk = Chunk(
        version_id=version.id,
        ordinal=0,
        content=f"Auditable course evidence {suffix}.",
        char_count=34,
    )
    session.add(chunk)
    await session.flush()

    concept = ConceptCandidate(
        course_id=course_id,
        name=f"Concept {suffix}",
        description="A reviewed concept used by history tests.",
        source_chunk_id=chunk.id,
        evidence=chunk.content,
        extraction_model="test-fixture",
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
        explanation="The reviewed source supports A.",
        difficulty=QuizDifficulty.MEDIUM,
        status=ReviewStatus.APPROVED,
        generation_model="test-fixture",
    )
    session.add(quiz)
    await session.flush()
    return concept, quiz


async def test_student_history_is_scoped_replayable_and_revoked_on_leave(
    client, app_instance
):
    _, teacher_tokens = await register_and_login(
        client, "history-owner@example.com", role="TEACHER"
    )
    student, student_tokens = await register_and_login(
        client, "history-student@example.com", role="STUDENT"
    )
    other_student, other_student_tokens = await register_and_login(
        client, "history-other-student@example.com", role="STUDENT"
    )
    course = await create_course(
        client,
        teacher_tokens,
        code="HISTORY-A",
        name="History Course A",
    )
    other_course = await create_course(
        client,
        teacher_tokens,
        template="OPERATING_SYSTEMS",
        code="HISTORY-B",
        name="History Course B",
    )
    await _join_course(client, student_tokens, course["invite_code"])
    await _join_course(client, student_tokens, other_course["invite_code"])
    await _join_course(client, other_student_tokens, course["invite_code"])

    course_id = uuid.UUID(course["id"])
    other_course_id = uuid.UUID(other_course["id"])
    student_id = uuid.UUID(student["id"])
    other_student_id = uuid.UUID(other_student["id"])
    occurred_at = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)

    async with app_instance.state.session_factory() as session:
        concept, quiz = await _seed_course_material(session, course_id, suffix="a")
        _, other_quiz = await _seed_course_material(
            session, other_course_id, suffix="b"
        )

        own_session = ChatSession(
            course_id=course_id,
            student_id=student_id,
            title="My course A session",
            summary="Only the signed-in student's course A history.",
            index_version=1,
            created_at=occurred_at,
        )
        other_student_session = ChatSession(
            course_id=course_id,
            student_id=other_student_id,
            title="SECRET_OTHER_STUDENT_SESSION",
            index_version=1,
            created_at=occurred_at + timedelta(minutes=1),
        )
        other_course_session = ChatSession(
            course_id=other_course_id,
            student_id=student_id,
            title="SECRET_OTHER_COURSE_SESSION",
            index_version=1,
            created_at=occurred_at + timedelta(minutes=2),
        )
        session.add_all([own_session, other_student_session, other_course_session])
        await session.flush()
        session.add_all(
            [
                Message(
                    session_id=own_session.id,
                    role=MessageRole.USER,
                    content="Explain the reviewed concept.",
                    status=MessageStatus.COMPLETED,
                    created_at=occurred_at,
                ),
                Message(
                    session_id=own_session.id,
                    role=MessageRole.ASSISTANT,
                    content="A grounded answer with course evidence.",
                    status=MessageStatus.COMPLETED,
                    created_at=occurred_at + timedelta(seconds=1),
                ),
                Message(
                    session_id=other_student_session.id,
                    role=MessageRole.USER,
                    content="SECRET_OTHER_STUDENT_MESSAGE",
                    status=MessageStatus.COMPLETED,
                    created_at=occurred_at,
                ),
                Message(
                    session_id=other_course_session.id,
                    role=MessageRole.USER,
                    content="SECRET_OTHER_COURSE_MESSAGE",
                    status=MessageStatus.COMPLETED,
                    created_at=occurred_at,
                ),
            ]
        )

        correct_attempt = QuizAttempt(
            quiz_item_id=quiz.id,
            student_id=student_id,
            answer="A",
            correct=True,
            idempotency_key="history-correct-a",
            weight=0.75,
            status=QuizAttemptStatus.GRADED,
            submitted_at=occurred_at + timedelta(minutes=3),
            graded_at=occurred_at + timedelta(minutes=3),
        )
        incorrect_attempt = QuizAttempt(
            quiz_item_id=quiz.id,
            student_id=student_id,
            answer="B",
            correct=False,
            idempotency_key="history-incorrect-a",
            weight=1.25,
            status=QuizAttemptStatus.GRADED,
            submitted_at=occurred_at + timedelta(minutes=4),
            graded_at=occurred_at + timedelta(minutes=4),
        )
        other_student_attempt = QuizAttempt(
            quiz_item_id=quiz.id,
            student_id=other_student_id,
            answer="A",
            correct=True,
            idempotency_key="history-other-student",
            weight=1.0,
            status=QuizAttemptStatus.GRADED,
            submitted_at=occurred_at + timedelta(minutes=5),
            graded_at=occurred_at + timedelta(minutes=5),
        )
        other_course_attempt = QuizAttempt(
            quiz_item_id=other_quiz.id,
            student_id=student_id,
            answer="A",
            correct=True,
            idempotency_key="history-other-course",
            weight=1.0,
            status=QuizAttemptStatus.GRADED,
            submitted_at=occurred_at + timedelta(minutes=6),
            graded_at=occurred_at + timedelta(minutes=6),
        )
        session.add_all(
            [
                correct_attempt,
                incorrect_attempt,
                other_student_attempt,
                other_course_attempt,
            ]
        )
        await session.commit()

        own_session_id = own_session.id
        hidden_session_ids = {
            other_student_session.id,
            other_course_session.id,
        }
        own_attempt_ids = [correct_attempt.id, incorrect_attempt.id]
        hidden_attempt_ids = {
            other_student_attempt.id,
            other_course_attempt.id,
        }
        concept_id = concept.id

    sessions_response = await client.get(
        f"/api/v1/courses/{course['id']}/chat/sessions",
        headers=auth_headers(student_tokens),
    )
    assert sessions_response.status_code == 200, sessions_response.text
    assert [item["id"] for item in sessions_response.json()["data"]] == [
        str(own_session_id)
    ]
    [session_summary] = sessions_response.json()["data"]
    assert session_summary["message_count"] == 2
    assert session_summary["user_message_count"] == 1
    assert session_summary["assistant_message_count"] == 1
    assert "SECRET_OTHER" not in sessions_response.text

    history_response = await client.get(
        f"/api/v1/courses/{course['id']}/learning-history",
        headers=auth_headers(student_tokens),
    )
    assert history_response.status_code == 200, history_response.text
    history = history_response.json()["data"]
    assert history["course_id"] == course["id"]
    assert [item["id"] for item in history["chat_sessions"]] == [str(own_session_id)]
    assert [item["id"] for item in history["quiz_attempts"]] == [
        str(attempt_id) for attempt_id in own_attempt_ids
    ]
    assert {(item["correct"], item["weight"]) for item in history["quiz_attempts"]} == {
        (True, 0.75),
        (False, 1.25),
    }
    assert all(
        str(hidden_id) not in history_response.text for hidden_id in hidden_session_ids
    )
    assert all(
        str(hidden_id) not in history_response.text for hidden_id in hidden_attempt_ids
    )

    first_update, second_update = history["mastery_updates"]
    assert first_update["attempt_id"] == str(own_attempt_ids[0])
    assert first_update["concept_id"] == str(concept_id)
    assert first_update["before_mastery"] == pytest.approx(0.5)
    assert first_update["after_mastery"] == pytest.approx(1.75 / 2.75)
    assert first_update["alpha_after"] == pytest.approx(1.75)
    assert first_update["beta_after"] == pytest.approx(1.0)
    assert first_update["attempt_count"] == 1
    assert second_update["attempt_id"] == str(own_attempt_ids[1])
    assert second_update["before_mastery"] == pytest.approx(1.75 / 2.75)
    assert second_update["after_mastery"] == pytest.approx(1.75 / 4.0)
    assert second_update["alpha_after"] == pytest.approx(1.75)
    assert second_update["beta_after"] == pytest.approx(2.25)
    assert second_update["attempt_count"] == 2

    left = await client.post(
        f"/api/v1/courses/{course['id']}/leave",
        headers=auth_headers(student_tokens),
    )
    assert left.status_code == 200, left.text
    assert left.json()["data"]["status"] == "LEFT"

    course_after_leave = await client.get(
        f"/api/v1/courses/{course['id']}",
        headers=auth_headers(student_tokens),
    )
    history_after_leave = await client.get(
        f"/api/v1/courses/{course['id']}/learning-history",
        headers=auth_headers(student_tokens),
    )
    sessions_after_leave = await client.get(
        f"/api/v1/courses/{course['id']}/chat/sessions",
        headers=auth_headers(student_tokens),
    )
    for denied in (course_after_leave, history_after_leave, sessions_after_leave):
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"]["code"] == "COURSE_ACCESS_DENIED"

    async with app_instance.state.session_factory() as session:
        enrollment = await session.scalar(
            select(Enrollment).where(
                Enrollment.course_id == course_id,
                Enrollment.student_id == student_id,
            )
        )
        assert enrollment is not None
        assert enrollment.status == EnrollmentStatus.LEFT
        retained_session_ids = set(
            (
                await session.scalars(
                    select(ChatSession.id).where(
                        ChatSession.course_id == course_id,
                        ChatSession.student_id == student_id,
                    )
                )
            ).all()
        )
        retained_attempt_ids = set(
            (
                await session.scalars(
                    select(QuizAttempt.id)
                    .join(QuizItem, QuizItem.id == QuizAttempt.quiz_item_id)
                    .where(
                        QuizAttempt.student_id == student_id,
                        QuizItem.course_id == course_id,
                    )
                )
            ).all()
        )
    assert own_session_id in retained_session_ids
    assert set(own_attempt_ids).issubset(retained_attempt_ids)

    remaining_courses = await client.get(
        "/api/v1/courses", headers=auth_headers(student_tokens)
    )
    assert remaining_courses.status_code == 200
    assert [item["id"] for item in remaining_courses.json()["data"]] == [
        other_course["id"]
    ]


async def test_unenrolled_student_and_teacher_cannot_leave(client):
    _, teacher_tokens = await register_and_login(
        client, "leave-owner@example.com", role="TEACHER"
    )
    _, outsider_tokens = await register_and_login(
        client, "leave-outsider@example.com", role="STUDENT"
    )
    course = await create_course(
        client,
        teacher_tokens,
        code="LEAVE-PERMISSIONS",
        name="Leave Permissions",
    )

    outsider_leave = await client.post(
        f"/api/v1/courses/{course['id']}/leave",
        headers=auth_headers(outsider_tokens),
    )
    assert outsider_leave.status_code == 403
    assert outsider_leave.json()["error"]["code"] == "COURSE_ACCESS_DENIED"

    teacher_leave = await client.post(
        f"/api/v1/courses/{course['id']}/leave",
        headers=auth_headers(teacher_tokens),
    )
    assert teacher_leave.status_code == 403
    assert teacher_leave.json()["error"]["code"] == "ROLE_FORBIDDEN"
