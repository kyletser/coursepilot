"""Version isolation: retrieval, publication, and pinned historical sessions."""

from __future__ import annotations

import json
import uuid

import pytest_asyncio
from sqlalchemy import select

from app.agent.service import CourseMaterialEvidenceRetriever, DatabaseLexicalRetriever
from app.models import (
    Chunk,
    CourseIndex,
    CourseIndexStatus,
    Document,
    DocumentVersion,
    DocumentVersionStatus,
    IndexComponentStatus,
    IngestionJob,
    IngestionStage,
)
from tests.helpers import auth_headers, create_course, register_and_login


async def _seed_document_version(
    app_instance,
    course_id: uuid.UUID,
    *,
    logical_name: str,
    content: str,
    version_number: int = 1,
    status: DocumentVersionStatus = DocumentVersionStatus.PUBLISHED,
    document_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Create (or extend) a document with one chunk and return (document_id, version_id)."""

    async with app_instance.state.session_factory() as session:
        if document_id is None:
            document = Document(course_id=course_id, logical_name=logical_name)
            session.add(document)
            await session.flush()
            document_id = document.id
        version = DocumentVersion(
            document_id=document_id,
            sha256=uuid.uuid4().hex + uuid.uuid4().hex,
            version=version_number,
            original_filename=f"{logical_name}.md",
            media_type="text/markdown",
            file_path=f"/tmp/{uuid.uuid4()}.md",
            size_bytes=len(content),
            status=status,
        )
        session.add(version)
        await session.flush()
        session.add(
            Chunk(
                version_id=version.id,
                ordinal=0,
                content=content,
                page=1,
                section_path=[logical_name],
                char_count=len(content),
            )
        )
        await session.commit()
        return document_id, version.id


def _events(response) -> list[tuple[str, dict]]:
    parsed: list[tuple[str, dict]] = []
    for block in response.text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        parsed.append((event, data))
    return parsed


@pytest_asyncio.fixture(autouse=True)
async def install_graph_routes(app_instance):
    from app.routers.graph import router as graph_router

    if not any(
        getattr(route, "path", None) == "/api/v1/courses/{course_id}/graph"
        for route in app_instance.routes
    ):
        app_instance.include_router(graph_router)


async def test_database_lexical_fallback_only_serves_covered_versions(
    client, app_instance
):
    _, teacher_tokens = await register_and_login(
        client, "fallback-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, teacher_tokens, code="FALLBACK-1")
    course_id = uuid.UUID(course["id"])
    document_id, version_v1 = await _seed_document_version(
        app_instance,
        course_id,
        logical_name="course-doc",
        content="旧版本内容：页面置换算法。",
    )
    _, _version_v2 = await _seed_document_version(
        app_instance,
        course_id,
        logical_name="course-doc",
        content="新版本内容：调度算法与页面置换算法。",
        version_number=2,
        document_id=document_id,
    )

    async with app_instance.state.session_factory() as session:
        scoped = DatabaseLexicalRetriever(
            session,
            course_id=course_id,
            index_version=1,
            covered_version_ids=[str(version_v1)],
        )
        hits = await scoped.search(
            course_id=str(course_id),
            index_version="1",
            query="页面置换算法",
            top_k=10,
        )
        v1_chunk = await _first_chunk_id(app_instance, version_v1)
        assert {hit.chunk_id for hit in hits} == {str(v1_chunk)}

        legacy_without_manifest = DatabaseLexicalRetriever(
            session,
            course_id=course_id,
            index_version=1,
        )
        legacy_hits = await legacy_without_manifest.search(
            course_id=str(course_id),
            index_version="1",
            query="页面置换算法",
            top_k=10,
        )
    # A pre-migration index has no trustworthy corpus manifest. It must fail
    # closed instead of admitting every published version into a pinned chat.
    assert legacy_hits == []


async def test_authoritative_hydration_rejects_candidates_outside_index_manifest(
    client, app_instance
):
    _, tokens = await register_and_login(
        client, "hydrate-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, tokens, code="HYDRATE-1")
    course_id = uuid.UUID(course["id"])
    _, covered_version = await _seed_document_version(
        app_instance, course_id, logical_name="covered", content="covered text"
    )
    _, uncovered_version = await _seed_document_version(
        app_instance, course_id, logical_name="uncovered", content="uncovered text"
    )
    uncovered_chunk = await _first_chunk_id(app_instance, uncovered_version)
    async with app_instance.state.session_factory() as session:
        session.add(
            CourseIndex(
                course_id=course_id,
                version=1,
                dense_status=IndexComponentStatus.READY,
                lexical_status=IndexComponentStatus.READY,
                status=CourseIndexStatus.ACTIVE,
                covered_document_version_ids=[str(covered_version)],
            )
        )
        await session.commit()
        retriever = CourseMaterialEvidenceRetriever(
            session,
            backend=object(),
            course_id=course_id,
            index_version=1,
            trace_id=uuid.uuid4(),
        )
        hydrated = await retriever._hydrate(
            [{"chunk_id": str(uncovered_chunk), "score": 0.99}], top_k=8
        )
    assert hydrated == []


async def _first_chunk_id(app_instance, version_id: uuid.UUID) -> uuid.UUID:
    async with app_instance.state.session_factory() as session:
        return await session.scalar(
            select(Chunk.id).where(Chunk.version_id == version_id)
        )


async def test_archived_index_keeps_serving_its_pinned_chat_session(
    client, app_instance
):
    _, teacher_tokens = await register_and_login(
        client, "pinned-owner@example.com", role="TEACHER"
    )
    _student, student_tokens = await register_and_login(
        client, "pinned-student@example.com", role="STUDENT"
    )
    course = await create_course(client, teacher_tokens, code="PINNED-1")
    course_id = uuid.UUID(course["id"])
    await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    _, version_id = await _seed_document_version(
        app_instance,
        course_id,
        logical_name="操作系统课件",
        content="物理页框有限时，虚拟内存使用页面置换算法选择要换出的页面。",
    )
    async with app_instance.state.session_factory() as session:
        index = CourseIndex(
            course_id=course_id,
            version=1,
            dense_status=IndexComponentStatus.FAILED,
            lexical_status=IndexComponentStatus.READY,
            status=CourseIndexStatus.ACTIVE,
            covered_document_version_ids=[str(version_id)],
        )
        session.add(index)
        await session.commit()
        index_id = index.id

    created = await client.post(
        f"/api/v1/courses/{course_id}/chat/sessions",
        headers=auth_headers(student_tokens),
        json={"title": "页面置换"},
    )
    assert created.status_code == 201, created.text
    chat_session = created.json()["data"]

    # A new version is published: the pinned index becomes ARCHIVED.
    async with app_instance.state.session_factory() as session:
        stored = await session.get(CourseIndex, index_id)
        stored.status = CourseIndexStatus.ARCHIVED
        await session.commit()

    sent = await client.post(
        f"/api/v1/chat/sessions/{chat_session['id']}/messages",
        headers=auth_headers(student_tokens),
        json={"content": "为什么需要页面置换算法？"},
    )
    assert sent.status_code == 200, sent.text
    events = _events(sent)
    assert events[-1][0] == "done"
    assert any(name == "token" and "页面置换" in data["text"] for name, data in events)


async def test_publish_only_flips_document_versions_covered_by_the_target_index(
    client, app_instance
):
    _, teacher_tokens = await register_and_login(
        client, "coverage-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, teacher_tokens, code="COVERAGE-1")
    course_id = uuid.UUID(course["id"])
    _, covered_version = await _seed_document_version(
        app_instance,
        course_id,
        logical_name="covered-doc",
        content="已建立索引的内容。",
        status=DocumentVersionStatus.READY_FOR_REVIEW,
    )
    _, uncovered_version = await _seed_document_version(
        app_instance,
        course_id,
        logical_name="uncovered-doc",
        content="在索引构建之后才完成入库的内容。",
        status=DocumentVersionStatus.READY_FOR_REVIEW,
    )
    async with app_instance.state.session_factory() as session:
        session.add(
            CourseIndex(
                course_id=course_id,
                version=1,
                dense_status=IndexComponentStatus.READY,
                lexical_status=IndexComponentStatus.READY,
                status=CourseIndexStatus.READY,
                covered_document_version_ids=[str(covered_version)],
            )
        )
        for version_id in (covered_version, uncovered_version):
            session.add(
                IngestionJob(
                    version_id=version_id,
                    stage=IngestionStage.READY_FOR_REVIEW,
                    progress=100,
                )
            )
        await session.commit()

    published = await client.post(
        f"/api/v1/courses/{course_id}/graph/publish",
        headers=auth_headers(teacher_tokens),
    )
    assert published.status_code == 200, published.text
    payload = published.json()["data"]
    assert payload["published_document_version_ids"] == [str(covered_version)]

    async with app_instance.state.session_factory() as session:
        covered_status = await session.scalar(
            select(DocumentVersion.status).where(DocumentVersion.id == covered_version)
        )
        uncovered_status = await session.scalar(
            select(DocumentVersion.status).where(
                DocumentVersion.id == uncovered_version
            )
        )
    assert covered_status == DocumentVersionStatus.PUBLISHED
    # Content ingested after the index build waits for the next build cycle
    # instead of being published outside the serving corpus.
    assert uncovered_status == DocumentVersionStatus.READY_FOR_REVIEW


async def test_publish_without_recorded_coverage_requires_index_rebuild(
    client, app_instance
):
    _, teacher_tokens = await register_and_login(
        client, "legacy-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, teacher_tokens, code="COVERAGE-2")
    course_id = uuid.UUID(course["id"])
    _, version_id = await _seed_document_version(
        app_instance,
        course_id,
        logical_name="legacy-doc",
        content="历史构建产生的版本。",
        status=DocumentVersionStatus.READY_FOR_REVIEW,
    )
    async with app_instance.state.session_factory() as session:
        session.add(
            CourseIndex(
                course_id=course_id,
                version=1,
                dense_status=IndexComponentStatus.READY,
                lexical_status=IndexComponentStatus.READY,
                status=CourseIndexStatus.READY,
            )
        )
        session.add(
            IngestionJob(
                version_id=version_id,
                stage=IngestionStage.READY_FOR_REVIEW,
                progress=100,
            )
        )
        await session.commit()

    published = await client.post(
        f"/api/v1/courses/{course_id}/graph/publish",
        headers=auth_headers(teacher_tokens),
    )
    assert published.status_code == 409, published.text
    assert published.json()["error"]["code"] == "GRAPH_INDEX_COVERAGE_UNKNOWN"
    async with app_instance.state.session_factory() as session:
        status = await session.scalar(
            select(DocumentVersion.status).where(DocumentVersion.id == version_id)
        )
    assert status == DocumentVersionStatus.READY_FOR_REVIEW
