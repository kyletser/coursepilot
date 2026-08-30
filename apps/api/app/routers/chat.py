from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select

from app.agent import AgentCoreError, AgentStatus, Intent
from app.agent.service import build_retrieval_backend, run_trusted_turn
from app.dependencies import SessionDep, StudentUser
from app.errors import AppError, success_response
from app.learning.history import student_history_service
from app.models import (
    AgentIntent,
    ChatSession,
    ChatSessionStatus,
    Chunk,
    Citation,
    Course,
    CourseIndex,
    CourseIndexStatus,
    CourseStatus,
    Document,
    DocumentVersion,
    Enrollment,
    EnrollmentStatus,
    Message,
    MessageRole,
    MessageStatus,
    RecordStatus,
    User,
)

router = APIRouter(prefix="/api/v1", tags=["chat"])


class ChatSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(default="", max_length=255)

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return value.strip()


class ChatMessageCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=4000)
    requested_intent: Intent | None = None
    target_concept_ids: list[uuid.UUID] = Field(default_factory=list, max_length=16)

    @field_validator("content")
    @classmethod
    def content_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("content must not be blank")
        return value


def _session_data(record: ChatSession) -> dict[str, Any]:
    return {
        "id": record.id,
        "course_id": record.course_id,
        "student_id": record.student_id,
        "title": record.title,
        "summary": record.summary,
        "index_version": record.index_version,
        "status": record.status,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


async def _require_active_enrollment(
    session: SessionDep,
    *,
    course_id: uuid.UUID,
    student: User,
) -> Course:
    course = await session.scalar(select(Course).where(Course.id == course_id))
    if course is None:
        raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
    enrollment = await session.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == course_id,
            Enrollment.student_id == student.id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
    )
    if enrollment is None or course.status != CourseStatus.ACTIVE:
        raise AppError(
            403,
            "COURSE_ACCESS_DENIED",
            "You do not have access to this course",
        )
    return course


async def _owned_session(
    session: SessionDep,
    *,
    session_id: uuid.UUID,
    student: User,
    require_active_session: bool = False,
) -> ChatSession:
    record = await session.scalar(
        select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.deleted_at.is_(None),
        )
    )
    if record is None:
        raise AppError(404, "CHAT_SESSION_NOT_FOUND", "Chat session not found")
    if record.student_id != student.id:
        raise AppError(
            403,
            "CHAT_SESSION_ACCESS_DENIED",
            "You do not have access to this chat session",
        )
    await _require_active_enrollment(
        session, course_id=record.course_id, student=student
    )
    if require_active_session and record.status != ChatSessionStatus.ACTIVE:
        raise AppError(409, "CHAT_SESSION_ARCHIVED", "Chat session is archived")
    return record


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _retrieval_event(
    *,
    query: str,
    candidate_count: int,
    index_version: int,
    traces: tuple[dict[str, Any], ...],
) -> dict[str, Any]:
    degraded_routes: list[str] = []
    for trace in traces:
        for route in trace.get("degraded_routes", []):
            if isinstance(route, str) and route not in degraded_routes:
                degraded_routes.append(route)
    return {
        "query": query,
        "candidate_count": candidate_count,
        "index_version": str(index_version),
        "degraded": bool(degraded_routes),
        "degraded_routes": degraded_routes,
    }


@router.post("/courses/{course_id}/chat/sessions", status_code=201)
async def create_chat_session(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    student: StudentUser,
    payload: ChatSessionCreateRequest | None = None,
):
    await _require_active_enrollment(session, course_id=course_id, student=student)
    active_index = await session.scalar(
        select(CourseIndex)
        .where(
            CourseIndex.course_id == course_id,
            CourseIndex.status == CourseIndexStatus.ACTIVE,
            CourseIndex.deleted_at.is_(None),
        )
        .order_by(CourseIndex.version.desc())
    )
    if active_index is None:
        raise AppError(
            409,
            "COURSE_INDEX_UNAVAILABLE",
            "The course has no active index",
        )
    record = ChatSession(
        course_id=course_id,
        student_id=student.id,
        title=payload.title if payload else "",
        index_version=active_index.version,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return success_response(request, _session_data(record), status_code=201)


@router.get("/courses/{course_id}/chat/sessions")
async def list_chat_sessions(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    student: StudentUser,
):
    records = await student_history_service.list_chat_sessions(
        session, course_id, student
    )
    return success_response(request, records)


@router.get("/chat/sessions/{session_id}/messages")
async def get_chat_messages(
    session_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    student: StudentUser,
):
    chat_session = await _owned_session(session, session_id=session_id, student=student)
    messages = (
        await session.scalars(
            select(Message)
            .where(
                Message.session_id == session_id,
                Message.deleted_at.is_(None),
            )
            .order_by(Message.created_at, Message.id)
        )
    ).all()
    citations_by_message: dict[uuid.UUID, list[dict[str, Any]]] = {
        message.id: [] for message in messages
    }
    if messages:
        rows = (
            await session.execute(
                select(Citation, Chunk, DocumentVersion, Document)
                .join(Chunk, Chunk.id == Citation.chunk_id)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Citation.message_id.in_([message.id for message in messages]),
                    Citation.status == RecordStatus.ACTIVE,
                    Citation.deleted_at.is_(None),
                    Document.course_id == chat_session.course_id,
                )
                .order_by(Citation.message_id, Citation.label)
            )
        ).all()
        for citation, chunk, version, document in rows:
            citations_by_message[citation.message_id].append(
                {
                    "citation_id": citation.id,
                    "label": citation.label,
                    "claim_index": citation.claim_index,
                    "chunk_id": citation.chunk_id,
                    "quote": citation.quote,
                    "document": document.logical_name,
                    "document_version": str(version.version),
                    "section": " / ".join(chunk.section_path)
                    if chunk.section_path
                    else None,
                    "page": chunk.page,
                }
            )
    data = [
        {
            "id": message.id,
            "session_id": message.session_id,
            "role": message.role,
            "content": message.content,
            "intent": message.intent,
            "trace_id": message.trace_id,
            "status": message.status,
            "usage": message.usage,
            "error_code": message.error_code,
            "created_at": message.created_at,
            "citations": citations_by_message[message.id],
        }
        for message in messages
    ]
    return success_response(request, data)


@router.post("/chat/sessions/{session_id}/messages")
async def send_chat_message(
    session_id: uuid.UUID,
    payload: ChatMessageCreateRequest,
    request: Request,
    session: SessionDep,
    student: StudentUser,
):
    chat_session = await _owned_session(
        session,
        session_id=session_id,
        student=student,
        require_active_session=True,
    )
    index = await session.scalar(
        select(CourseIndex).where(
            CourseIndex.course_id == chat_session.course_id,
            CourseIndex.version == chat_session.index_version,
            # Historical sessions stay pinned to their creation version; the
            # index is ARCHIVED once a newer version is published but must
            # keep serving this session with its own corpus.
            CourseIndex.status.in_(
                (CourseIndexStatus.ACTIVE, CourseIndexStatus.ARCHIVED)
            ),
            CourseIndex.deleted_at.is_(None),
        )
    )
    if index is None:
        raise AppError(
            409,
            "SESSION_INDEX_UNAVAILABLE",
            "The chat session index is unavailable",
        )

    trace_id = uuid.uuid4()
    turn_started_at = datetime.now(UTC)
    requested_model_intent = (
        AgentIntent(payload.requested_intent.value)
        if payload.requested_intent is not None
        else None
    )
    user_message = Message(
        session_id=chat_session.id,
        role=MessageRole.USER,
        content=payload.content,
        intent=requested_model_intent,
        trace_id=trace_id,
        status=MessageStatus.COMPLETED,
        created_at=turn_started_at,
    )
    assistant_message = Message(
        session_id=chat_session.id,
        role=MessageRole.ASSISTANT,
        content="",
        intent=requested_model_intent,
        trace_id=trace_id,
        status=MessageStatus.PENDING,
        created_at=turn_started_at + timedelta(microseconds=1),
    )
    session.add_all([user_message, assistant_message])
    await session.flush()

    backend = build_retrieval_backend(request.app.state, session, index=index)
    chat_adapter = getattr(request.app.state, "chat_adapter", None)
    try:
        turn = await run_trusted_turn(
            session,
            backend=backend,
            chat_adapter=chat_adapter,
            course_id=chat_session.course_id,
            student_id=student.id,
            index_version=chat_session.index_version,
            query=payload.content,
            requested_intent=payload.requested_intent,
            target_concept_ids=payload.target_concept_ids,
            trace_id=trace_id,
        )
    except AgentCoreError as exc:
        assistant_message.content = str(exc)
        assistant_message.status = MessageStatus.FAILED
        assistant_message.error_code = exc.code
        assistant_message.usage = {"agent_status": "FAILED", "retrieval_traces": []}
        await session.commit()
        events = [
            _sse(
                "status",
                {"stage": "retrieving", "trace_id": str(trace_id)},
            ),
            _sse("error", {"code": exc.code, "message": str(exc)}),
        ]
    else:
        response = turn.response
        assistant_message.content = response.answer
        assistant_message.intent = AgentIntent(response.route.intent.value)
        assistant_message.error_code = response.error_code
        terminal_error = response.status in {
            AgentStatus.INSUFFICIENT_EVIDENCE,
            AgentStatus.RETRIEVAL_UNAVAILABLE,
            AgentStatus.BUDGET_EXCEEDED,
        }
        assistant_message.status = (
            MessageStatus.FAILED if terminal_error else MessageStatus.COMPLETED
        )
        assistant_message.usage = {
            "agent_status": response.status.value,
            "budget": response.budget.model_dump(mode="json"),
            "warnings": response.warnings,
            "retrieval_traces": list(turn.retrieval_traces),
        }
        if not chat_session.title:
            chat_session.title = payload.content[:80]

        citation_records: list[tuple[Any, Citation]] = []
        for source in response.citations:
            if not source.claim_indices:
                continue
            try:
                chunk_id = uuid.UUID(source.chunk_id)
            except ValueError:
                continue
            record = Citation(
                message_id=assistant_message.id,
                chunk_id=chunk_id,
                quote=source.quote,
                claim_index=source.claim_indices[0],
                label=source.label,
            )
            session.add(record)
            citation_records.append((source, record))
        await session.flush()
        await session.commit()

        retrieval_query = turn.retrieval_query or payload.content
        events = [
            _sse(
                "status",
                {"stage": "retrieving", "trace_id": str(trace_id)},
            ),
            _sse(
                "retrieval",
                _retrieval_event(
                    query=retrieval_query,
                    candidate_count=turn.candidate_count,
                    index_version=chat_session.index_version,
                    traces=turn.retrieval_traces,
                ),
            ),
        ]
        if terminal_error:
            events.append(
                _sse(
                    "error",
                    {
                        "code": response.error_code or "AGENT_FAILED",
                        "message": response.error_message or response.answer,
                    },
                )
            )
        else:
            events.append(_sse("token", {"text": response.answer}))
            for source, record in citation_records:
                events.append(
                    _sse(
                        "citation",
                        {
                            "citation_id": str(record.id),
                            "label": source.label,
                            "document": source.document,
                            "document_version": source.document_version,
                            "section": source.section,
                            "page": source.page,
                            "chunk_id": source.chunk_id,
                        },
                    )
                )
            events.append(
                _sse(
                    "done",
                    {
                        "message_id": str(assistant_message.id),
                        "intent": response.route.intent.value,
                        "usage": response.budget.model_dump(mode="json"),
                    },
                )
            )

    async def event_stream() -> AsyncIterator[str]:
        for event in events:
            yield event

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
