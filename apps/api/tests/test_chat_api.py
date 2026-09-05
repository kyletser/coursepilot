from __future__ import annotations

import json
import uuid

import pytest_asyncio
from sqlalchemy import select

from app.agent.schemas import AnswerDraft, ClaimDraft
from app.models import (
    Chunk,
    ConceptCandidate,
    CourseIndex,
    CourseIndexStatus,
    Document,
    DocumentVersion,
    DocumentVersionStatus,
    IndexComponentStatus,
    MasteryState,
    QuizDifficulty,
    QuizItem,
    ReviewStatus,
)
from app.routers import chat
from tests.helpers import auth_headers, create_course, register_and_login


@pytest_asyncio.fixture(autouse=True)
async def ensure_chat_router(app_instance):
    # The production app includes this router. Keeping this guard makes the API
    # slice independently testable while parallel bootstrap work is in progress.
    if not any(
        getattr(route, "path", "") == "/api/v1/courses/{course_id}/chat/sessions"
        for route in app_instance.routes
    ):
        app_instance.include_router(chat.router)


def _events(response) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        parsed.append((event, data))
    return parsed


async def _course_with_index(client, app_instance, *, suffix: str):
    _, teacher_tokens = await register_and_login(
        client, f"chat-owner-{suffix}@example.com", role="TEACHER"
    )
    student, student_tokens = await register_and_login(
        client, f"chat-student-{suffix}@example.com", role="STUDENT"
    )
    course = await create_course(client, teacher_tokens, code=f"CHAT-{suffix}")
    joined = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    assert joined.status_code == 201

    async with app_instance.state.session_factory() as db:
        document = Document(
            course_id=uuid.UUID(course["id"]), logical_name="操作系统课件"
        )
        db.add(document)
        await db.flush()
        version = DocumentVersion(
            document_id=document.id,
            sha256=(suffix * 64)[:64],
            version=1,
            original_filename="os.md",
            media_type="text/markdown",
            file_path=f"/tmp/{suffix}.md",
            size_bytes=100,
            status=DocumentVersionStatus.PUBLISHED,
        )
        db.add(version)
        await db.flush()
        db.add(
            Chunk(
                version_id=version.id,
                ordinal=0,
                content="物理页框有限时，虚拟内存使用页面置换算法选择要换出的页面。",
                page=42,
                section_path=["虚拟内存", "页面置换"],
                char_count=31,
            )
        )
        db.add(
            CourseIndex(
                course_id=uuid.UUID(course["id"]),
                version=1,
                dense_status=IndexComponentStatus.FAILED,
                lexical_status=IndexComponentStatus.READY,
                status=CourseIndexStatus.ACTIVE,
                covered_document_version_ids=[str(version.id)],
            )
        )
        await db.commit()
    return course, student, student_tokens


async def test_grounded_sse_answer_is_persisted_with_citation(client, app_instance):
    course, _, student_tokens = await _course_with_index(
        client, app_instance, suffix="a"
    )
    created = await client.post(
        f"/api/v1/courses/{course['id']}/chat/sessions",
        headers=auth_headers(student_tokens),
        json={"title": "页面置换"},
    )
    assert created.status_code == 201, created.text
    chat_session = created.json()["data"]
    assert chat_session["index_version"] == 1

    sent = await client.post(
        f"/api/v1/chat/sessions/{chat_session['id']}/messages",
        headers=auth_headers(student_tokens),
        json={"content": "为什么需要页面置换算法？"},
    )
    assert sent.status_code == 200, sent.text
    assert sent.headers["content-type"].startswith("text/event-stream")
    events = _events(sent)
    assert [name for name, _ in events] == [
        "status",
        "retrieval",
        "status",
        "token",
        "citation",
        "done",
    ]
    assert events[3][1]["text"].endswith("[1]")
    assert events[4][1]["document"] == "操作系统课件"
    assert events[4][1]["page"] == 42

    history = await client.get(
        f"/api/v1/chat/sessions/{chat_session['id']}/messages",
        headers=auth_headers(student_tokens),
    )
    assert history.status_code == 200
    messages = history.json()["data"]
    assert [item["role"] for item in messages] == ["USER", "ASSISTANT"]
    assert messages[1]["status"] == "COMPLETED"
    assert messages[1]["citations"][0]["label"] == 1
    assert messages[1]["usage"]["retrieval_traces"]


async def test_no_matching_course_evidence_returns_and_persists_refusal(
    client, app_instance
):
    course, _, student_tokens = await _course_with_index(
        client, app_instance, suffix="b"
    )
    created = await client.post(
        f"/api/v1/courses/{course['id']}/chat/sessions",
        headers=auth_headers(student_tokens),
    )
    chat_session = created.json()["data"]
    sent = await client.post(
        f"/api/v1/chat/sessions/{chat_session['id']}/messages",
        headers=auth_headers(student_tokens),
        json={"content": "quantum-zxy-9981"},
    )
    events = _events(sent)
    assert events[-1] == (
        "error",
        {
            "code": "INSUFFICIENT_EVIDENCE",
            "message": "课程资料中没有足够依据回答该问题。",
        },
    )
    history = await client.get(
        f"/api/v1/chat/sessions/{chat_session['id']}/messages",
        headers=auth_headers(student_tokens),
    )
    assistant = history.json()["data"][-1]
    assert assistant["status"] == "FAILED"
    assert assistant["error_code"] == "INSUFFICIENT_EVIDENCE"
    assert assistant["citations"] == []


async def test_another_active_student_cannot_read_or_write_a_session(
    client, app_instance
):
    course, _, owner_tokens = await _course_with_index(client, app_instance, suffix="c")
    _, other_tokens = await register_and_login(
        client, "chat-other-c@example.com", role="STUDENT"
    )
    await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(other_tokens),
        json={"invite_code": course["invite_code"]},
    )
    created = await client.post(
        f"/api/v1/courses/{course['id']}/chat/sessions",
        headers=auth_headers(owner_tokens),
    )
    session_id = created.json()["data"]["id"]

    denied_read = await client.get(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_headers(other_tokens),
    )
    denied_write = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_headers(other_tokens),
        json={"content": "解释页面置换"},
    )
    assert denied_read.status_code == denied_write.status_code == 403
    assert (
        denied_read.json()["error"]["code"]
        == denied_write.json()["error"]["code"]
        == "CHAT_SESSION_ACCESS_DENIED"
    )


async def test_business_intents_execute_reviewed_learning_services(
    client, app_instance
):
    course, student, student_tokens = await _course_with_index(
        client, app_instance, suffix="d"
    )
    course_id = uuid.UUID(course["id"])
    async with app_instance.state.session_factory() as db:
        index = await db.scalar(
            select(CourseIndex).where(CourseIndex.course_id == course_id)
        )
        chunk = await db.scalar(
            select(Chunk)
            .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(Document.course_id == course_id)
        )
        concept = ConceptCandidate(
            course_id=course_id,
            index_id=index.id,
            name="页面置换",
            description="虚拟内存中的页面替换策略",
            aliases=[],
            source_chunk_id=chunk.id,
            evidence=chunk.content,
            extraction_model="test",
            confidence=0.95,
            status=ReviewStatus.APPROVED,
        )
        db.add(concept)
        await db.flush()
        db.add_all(
            [
                QuizItem(
                    course_id=course_id,
                    concept_id=concept.id,
                    source_chunk_id=chunk.id,
                    question="页面置换算法选择什么？",
                    options=["换出的页面", "CPU 指令"],
                    answer="换出的页面",
                    explanation="课程资料明确说明选择换出的页面。",
                    difficulty=QuizDifficulty.EASY,
                    status=ReviewStatus.APPROVED,
                    generation_model="test",
                ),
                MasteryState(
                    course_id=course_id,
                    student_id=uuid.UUID(student["id"]),
                    concept_id=concept.id,
                    alpha=1,
                    beta=3,
                    attempt_count=2,
                ),
            ]
        )
        await db.commit()
        concept_id = concept.id

    created = await client.post(
        f"/api/v1/courses/{course['id']}/chat/sessions",
        headers=auth_headers(student_tokens),
    )
    session_id = created.json()["data"]["id"]
    cases = [
        ("QUIZ", [], "页面置换算法选择什么"),
        ("LEARNING_PATH", [str(concept_id)], "建议学习顺序"),
        ("DIAGNOSE", [], "掌握度 25%"),
    ]
    for intent, target_ids, expected_text in cases:
        sent = await client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=auth_headers(student_tokens),
            json={
                "content": "请执行学习动作",
                "requested_intent": intent,
                "target_concept_ids": target_ids,
            },
        )
        tokens = "".join(
            data["text"] for name, data in _events(sent) if name == "token"
        )
        assert expected_text in tokens, _events(sent)
        assert "确定性业务处理器" not in tokens


class _TwoClaimAdapter:
    def generate(self, _prompt):
        return AnswerDraft(
            claims=[
                ClaimDraft(text="虚拟内存使用页面置换算法。", citation_labels=[1]),
                ClaimDraft(text="页面置换算法选择要换出的页面。", citation_labels=[1]),
            ]
        )


async def test_history_preserves_all_claim_bindings_for_one_citation(
    client, app_instance
):
    course, _, student_tokens = await _course_with_index(
        client, app_instance, suffix="e"
    )
    app_instance.state.chat_adapter = _TwoClaimAdapter()
    created = await client.post(
        f"/api/v1/courses/{course['id']}/chat/sessions",
        headers=auth_headers(student_tokens),
    )
    session_id = created.json()["data"]["id"]
    sent = await client.post(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_headers(student_tokens),
        json={"content": "页面置换算法有什么作用？"},
    )
    assert sent.status_code == 200
    history = await client.get(
        f"/api/v1/chat/sessions/{session_id}/messages",
        headers=auth_headers(student_tokens),
    )
    citation = history.json()["data"][-1]["citations"][0]
    assert citation["claim_index"] == 0
    assert citation["claim_indices"] == [0, 1]
