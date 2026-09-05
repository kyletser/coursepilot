from __future__ import annotations

import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.ingestion.pipeline import run_ingestion_pipeline
from app.models import (
    Chunk,
    ConceptCandidate,
    CourseIndex,
    CourseIndexStatus,
    DocumentVersion,
    DocumentVersionStatus,
    IndexComponentStatus,
    IngestionJob,
    IngestionStage,
)
from app.retrieval import ModelDependencyUnavailableError
from app.routers import documents
from tests.helpers import auth_headers, create_course, register_and_login


class FakeEmbeddingAdapter:
    async def embed_documents(self, texts):
        return [[0.001] * 1024 for _text in texts]


class UnavailableEmbeddingAdapter:
    async def embed_documents(self, texts):
        raise ModelDependencyUnavailableError("test dependency outage")


def _enable_documents_api(app_instance, tmp_path) -> None:
    if not any(
        getattr(route, "path", None) == "/api/v1/courses/{course_id}/documents"
        for route in app_instance.routes
    ):
        app_instance.include_router(documents.router)
    app_instance.state.settings.upload_root = tmp_path / "uploads"
    app_instance.state.settings.index_root = tmp_path / "indexes"
    app_instance.state.ingestion_dispatcher = lambda _job_id: None


async def _upload_txt(client, tokens, course_id: str):
    content = (
        "第一章 栈\n\n栈是一种后进先出的线性结构。入栈和出栈都只发生在栈顶。"
    ).encode()
    return await client.post(
        f"/api/v1/courses/{course_id}/documents",
        headers=auth_headers(tokens),
        files={"file": ("../../stack.txt", content, "application/octet-stream")},
    )


async def test_txt_upload_sha_idempotency_and_pipeline(client, app_instance, tmp_path):
    _enable_documents_api(app_instance, tmp_path)
    _, teacher_tokens = await register_and_login(
        client, "document-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, teacher_tokens)

    uploaded = await _upload_txt(client, teacher_tokens, course["id"])
    assert uploaded.status_code == 201, uploaded.text
    record = uploaded.json()["data"]
    assert record["duplicate"] is False
    assert record["logical_name"] == "stack"
    assert record["latest_version"]["original_filename"] == "stack.txt"
    assert record["ingestion_job"]["stage"] == "QUEUED"
    queued = await client.get(
        f"/api/v1/ingestion-jobs/{record['ingestion_job']['id']}",
        headers=auth_headers(teacher_tokens),
    )
    assert queued.status_code == 200
    assert queued.json()["data"]["stage"] == "QUEUED"

    stored_path: Path
    async with app_instance.state.session_factory() as session:
        version = await session.get(
            DocumentVersion, uuid.UUID(record["latest_version"]["id"])
        )
        assert version is not None
        stored_path = Path(version.file_path)
        assert stored_path.exists()
        assert stored_path.name != "stack.txt"

    repeated = await client.post(
        f"/api/v1/courses/{course['id']}/documents",
        headers=auth_headers(teacher_tokens),
        files={
            "file": (
                "renamed.txt",
                stored_path.read_bytes(),
                "text/plain",
            )
        },
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["data"]["duplicate"] is True
    assert (
        repeated.json()["data"]["latest_version"]["id"]
        == record["latest_version"]["id"]
    )

    result = await run_ingestion_pipeline(
        record["ingestion_job"]["id"],
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        embedding_adapter=FakeEmbeddingAdapter(),
    )
    assert result["stage"] == "READY_FOR_REVIEW"
    ready = await client.get(
        f"/api/v1/ingestion-jobs/{record['ingestion_job']['id']}",
        headers=auth_headers(teacher_tokens),
    )
    assert ready.json()["data"]["stage"] == "READY_FOR_REVIEW"

    async with app_instance.state.session_factory() as session:
        job = await session.get(IngestionJob, uuid.UUID(record["ingestion_job"]["id"]))
        version = await session.get(
            DocumentVersion, uuid.UUID(record["latest_version"]["id"])
        )
        course_index = await session.scalar(
            select(CourseIndex).where(CourseIndex.course_id == uuid.UUID(course["id"]))
        )
        chunk_count = await session.scalar(
            select(func.count(Chunk.id)).where(Chunk.version_id == version.id)
        )
        candidate_count = await session.scalar(
            select(func.count(ConceptCandidate.id)).where(
                ConceptCandidate.index_id == course_index.id
            )
        )
        assert job.stage == IngestionStage.READY_FOR_REVIEW
        assert version.status == DocumentVersionStatus.READY_FOR_REVIEW
        assert course_index.status == CourseIndexStatus.READY
        assert course_index.dense_status == IndexComponentStatus.READY
        assert course_index.lexical_status == IndexComponentStatus.READY
        assert Path(course_index.lexical_path).exists()
        assert chunk_count == 1
        assert candidate_count == 1

    listing = await client.get(
        f"/api/v1/courses/{course['id']}/documents",
        headers=auth_headers(teacher_tokens),
    )
    assert listing.status_code == 200
    assert len(listing.json()["data"]) == 1
    versions = await client.get(
        f"/api/v1/documents/{record['id']}/versions",
        headers=auth_headers(teacher_tokens),
    )
    assert versions.status_code == 200
    assert len(versions.json()["data"]) == 1


async def test_degraded_ready_job_retries_same_version_and_index(
    client, app_instance, tmp_path
):
    _enable_documents_api(app_instance, tmp_path)
    _, teacher_tokens = await register_and_login(
        client, "degraded-retry-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, teacher_tokens)
    uploaded = await _upload_txt(client, teacher_tokens, course["id"])
    record = uploaded.json()["data"]
    job_id = record["ingestion_job"]["id"]
    version_id = record["latest_version"]["id"]

    first_result = await run_ingestion_pipeline(
        job_id,
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        embedding_adapter=UnavailableEmbeddingAdapter(),
    )
    assert first_result["stage"] == "READY_FOR_REVIEW"
    original_index_id = first_result["course_index_id"]

    async with app_instance.state.session_factory() as session:
        course_index = await session.get(CourseIndex, uuid.UUID(original_index_id))
        assert course_index is not None
        assert course_index.dense_status == IndexComponentStatus.PENDING
        assert course_index.lexical_status == IndexComponentStatus.READY

    retried = await client.post(
        f"/api/v1/ingestion-jobs/{job_id}/retry",
        headers=auth_headers(teacher_tokens),
    )
    assert retried.status_code == 200, retried.text
    retry_payload = retried.json()["data"]
    assert retry_payload["stage"] == "QUEUED"
    assert retry_payload["retry_count"] == 1

    async with app_instance.state.session_factory() as session:
        job = await session.get(IngestionJob, uuid.UUID(job_id))
        course_index = await session.get(CourseIndex, uuid.UUID(original_index_id))
        assert job is not None
        assert course_index is not None
        assert job.version_id == uuid.UUID(version_id)
        assert job.stage_details["last_safe_stage"] == "CHUNKING"
        assert job.stage_details["recovery_history"][-1]["reason"] == (
            "DEGRADED_INDEX_COMPONENTS"
        )
        assert course_index.status == CourseIndexStatus.BUILDING

    second_result = await run_ingestion_pipeline(
        job_id,
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        embedding_adapter=FakeEmbeddingAdapter(),
    )
    assert second_result["stage"] == "READY_FOR_REVIEW"
    assert second_result["course_index_id"] == original_index_id

    async with app_instance.state.session_factory() as session:
        indexes = (
            await session.scalars(
                select(CourseIndex).where(
                    CourseIndex.course_id == uuid.UUID(course["id"])
                )
            )
        ).all()
        job = await session.get(IngestionJob, uuid.UUID(job_id))
        version = await session.get(DocumentVersion, uuid.UUID(version_id))
        assert len(indexes) == 1
        assert indexes[0].dense_status == IndexComponentStatus.READY
        assert indexes[0].lexical_status == IndexComponentStatus.READY
        assert job is not None and job.stage == IngestionStage.READY_FOR_REVIEW
        assert version is not None
        assert version.status == DocumentVersionStatus.READY_FOR_REVIEW


async def test_document_management_and_chunk_access_follow_course_permissions(
    client, app_instance, tmp_path
):
    _enable_documents_api(app_instance, tmp_path)
    _, owner_tokens = await register_and_login(
        client, "chunk-owner@example.com", role="TEACHER"
    )
    _, other_teacher_tokens = await register_and_login(
        client, "chunk-other-teacher@example.com", role="TEACHER"
    )
    _, enrolled_tokens = await register_and_login(
        client, "chunk-enrolled@example.com", role="STUDENT"
    )
    _, outsider_tokens = await register_and_login(
        client, "chunk-outsider@example.com", role="STUDENT"
    )
    course = await create_course(client, owner_tokens)
    await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(enrolled_tokens),
        json={"invite_code": course["invite_code"]},
    )
    uploaded = await _upload_txt(client, owner_tokens, course["id"])
    record = uploaded.json()["data"]
    await run_ingestion_pipeline(
        record["ingestion_job"]["id"],
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        embedding_adapter=FakeEmbeddingAdapter(),
    )
    async with app_instance.state.session_factory() as session:
        chunk_id = await session.scalar(
            select(Chunk.id).where(
                Chunk.version_id == uuid.UUID(record["latest_version"]["id"])
            )
        )
        version = await session.get(
            DocumentVersion, uuid.UUID(record["latest_version"]["id"])
        )
        version.status = DocumentVersionStatus.PUBLISHED
        await session.commit()

    other_teacher = await client.get(
        f"/api/v1/courses/{course['id']}/documents",
        headers=auth_headers(other_teacher_tokens),
    )
    assert other_teacher.status_code == 403
    assert other_teacher.json()["error"]["code"] == "COURSE_ACCESS_DENIED"

    student_management = await client.get(
        f"/api/v1/courses/{course['id']}/documents",
        headers=auth_headers(enrolled_tokens),
    )
    assert student_management.status_code == 403
    assert student_management.json()["error"]["code"] == "ROLE_FORBIDDEN"

    cross_upload = await _upload_txt(client, other_teacher_tokens, course["id"])
    assert cross_upload.status_code == 403
    student_upload = await _upload_txt(client, enrolled_tokens, course["id"])
    assert student_upload.status_code == 403

    for tokens in (owner_tokens, enrolled_tokens):
        allowed = await client.get(
            f"/api/v1/chunks/{chunk_id}", headers=auth_headers(tokens)
        )
        assert allowed.status_code == 200
        assert allowed.json()["data"]["content"].startswith("栈是一种")
        assert "embedding" not in allowed.text

    for tokens in (other_teacher_tokens, outsider_tokens):
        denied = await client.get(
            f"/api/v1/chunks/{chunk_id}", headers=auth_headers(tokens)
        )
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "COURSE_ACCESS_DENIED"
