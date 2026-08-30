from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.learning.service import learning_service
from app.models import (
    ChatSession,
    ConceptCandidate,
    Message,
    MessageRole,
    QuizAttempt,
    QuizAttemptStatus,
    QuizItem,
    User,
)


def _preview(content: str, *, limit: int = 180) -> str:
    normalized = " ".join(content.split())
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1]}…"


class StudentHistoryService:
    """Course-scoped history assembled only from persisted, auditable records."""

    async def list_chat_sessions(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        student: User,
    ) -> list[dict[str, Any]]:
        await learning_service.require_active_enrollment(session, course_id, student)
        return await self._chat_session_summaries(session, course_id, student.id)

    async def get_learning_history(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        student: User,
    ) -> dict[str, Any]:
        await learning_service.require_active_enrollment(session, course_id, student)
        chat_sessions = await self._chat_session_summaries(
            session, course_id, student.id
        )
        attempt_rows = (
            await session.execute(
                select(QuizAttempt, QuizItem, ConceptCandidate)
                .join(QuizItem, QuizItem.id == QuizAttempt.quiz_item_id)
                .join(
                    ConceptCandidate,
                    ConceptCandidate.id == QuizItem.concept_id,
                )
                .where(
                    QuizAttempt.student_id == student.id,
                    QuizItem.course_id == course_id,
                    ConceptCandidate.course_id == course_id,
                )
                .order_by(
                    QuizAttempt.submitted_at.asc(),
                    QuizAttempt.id.asc(),
                )
            )
        ).all()

        # QuizAttempt stores the immutable correctness and weight used by the
        # mastery transaction. Replaying those records gives an explainable
        # per-attempt history without inventing snapshots that were never stored.
        running: defaultdict[uuid.UUID, list[float]] = defaultdict(
            lambda: [1.0, 1.0, 0.0]
        )
        attempts: list[dict[str, Any]] = []
        mastery_updates: list[dict[str, Any]] = []
        for attempt, quiz, concept in attempt_rows:
            attempts.append(
                {
                    "id": attempt.id,
                    "quiz_item_id": quiz.id,
                    "concept_id": concept.id,
                    "concept_name": concept.name,
                    "question": quiz.question,
                    "answer": attempt.answer,
                    "correct": attempt.correct,
                    "correct_answer": quiz.answer,
                    "explanation": quiz.explanation,
                    "difficulty": quiz.difficulty,
                    "weight": attempt.weight,
                    "status": attempt.status,
                    "submitted_at": attempt.submitted_at,
                    "graded_at": attempt.graded_at,
                }
            )
            if attempt.status != QuizAttemptStatus.GRADED:
                continue

            alpha, beta, attempt_count = running[concept.id]
            before_mastery = alpha / (alpha + beta)
            if attempt.correct:
                alpha += attempt.weight
            else:
                beta += attempt.weight
            attempt_count += 1
            after_mastery = alpha / (alpha + beta)
            running[concept.id] = [alpha, beta, attempt_count]
            mastery_updates.append(
                {
                    "attempt_id": attempt.id,
                    "concept_id": concept.id,
                    "concept_name": concept.name,
                    "correct": attempt.correct,
                    "weight": attempt.weight,
                    "before_mastery": before_mastery,
                    "after_mastery": after_mastery,
                    "delta": after_mastery - before_mastery,
                    "alpha_after": alpha,
                    "beta_after": beta,
                    "attempt_count": int(attempt_count),
                    "occurred_at": attempt.graded_at or attempt.submitted_at,
                }
            )

        return {
            "course_id": course_id,
            "chat_sessions": chat_sessions,
            "quiz_attempts": attempts,
            "mastery_updates": mastery_updates,
        }

    async def _chat_session_summaries(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        student_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        records = list(
            (
                await session.scalars(
                    select(ChatSession)
                    .where(
                        ChatSession.course_id == course_id,
                        ChatSession.student_id == student_id,
                        ChatSession.deleted_at.is_(None),
                    )
                    .order_by(ChatSession.created_at.desc(), ChatSession.id.desc())
                )
            ).all()
        )
        if not records:
            return []

        record_ids = [record.id for record in records]
        messages = list(
            (
                await session.scalars(
                    select(Message)
                    .where(
                        Message.session_id.in_(record_ids),
                        Message.deleted_at.is_(None),
                    )
                    .order_by(Message.created_at.asc(), Message.id.asc())
                )
            ).all()
        )
        by_session: defaultdict[uuid.UUID, list[Message]] = defaultdict(list)
        for message in messages:
            by_session[message.session_id].append(message)

        summaries: list[dict[str, Any]] = []
        for record in records:
            session_messages = by_session[record.id]
            user_messages = [
                message
                for message in session_messages
                if message.role == MessageRole.USER
            ]
            assistant_messages = [
                message
                for message in session_messages
                if message.role == MessageRole.ASSISTANT
            ]
            latest_message = session_messages[-1] if session_messages else None
            first_user_message = user_messages[0] if user_messages else None
            summaries.append(
                {
                    "id": record.id,
                    "title": record.title,
                    "summary": record.summary,
                    "index_version": record.index_version,
                    "status": record.status,
                    "message_count": len(session_messages),
                    "user_message_count": len(user_messages),
                    "assistant_message_count": len(assistant_messages),
                    "first_user_message_preview": (
                        _preview(first_user_message.content)
                        if first_user_message is not None
                        else ""
                    ),
                    "latest_message_preview": (
                        _preview(latest_message.content)
                        if latest_message is not None
                        else ""
                    ),
                    "last_message_at": (
                        latest_message.created_at
                        if latest_message is not None
                        else None
                    ),
                    "created_at": record.created_at,
                    "updated_at": record.updated_at,
                }
            )
        return summaries


student_history_service = StudentHistoryService()
