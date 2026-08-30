from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import os
import secrets
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import CurrentUser, SessionDep, TeacherUser
from app.errors import AppError, success_response
from app.ingestion import DocumentFormat, IngestionError, IngestionErrorCode
from app.ingestion.parsers import detect_document_format
from app.models import (
    Chunk,
    Course,
    CourseStatus,
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    Enrollment,
    EnrollmentStatus,
    IngestionJob,
    IngestionStage,
    RecordStatus,
    User,
    UserRole,
)
from app.tasks.ingestion import enqueue_ingestion_job

router = APIRouter(tags=["documents"])
logger = logging.getLogger(__name__)
_ABSOLUTE_MAX_UPLOAD_BYTES = 50 * 1024 * 1024
_READ_SIZE = 1024 * 1024

_FORMAT_EXTENSION = {
    DocumentFormat.PDF: ".pdf",
    DocumentFormat.DOCX: ".docx",
    DocumentFormat.PPTX: ".pptx",
    DocumentFormat.MARKDOWN: ".md",
    DocumentFormat.TXT: ".txt",
}
_FORMAT_MEDIA_TYPE = {
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    DocumentFormat.PPTX: (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    DocumentFormat.MARKDOWN: "text/markdown",
    DocumentFormat.TXT: "text/plain",
}


def _safe_original_filename(value: str | None) -> str:
    name = (value or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = name.replace("\x00", "").strip()
    if not name or name in {".", ".."}:
        raise AppError(422, "INVALID_FILENAME", "A valid filename is required")
    suffix = Path(name).suffix[:20]
    stem_limit = max(1, 255 - len(suffix))
    return f"{Path(name).stem[:stem_limit]}{suffix}"


def _logical_name(value: str | None, original_filename: str) -> str:
    logical = (value if value is not None else Path(original_filename).stem).strip()
    logical = logical.replace("\x00", "")[:255]
    if not logical:
        raise AppError(
            422,
            "INVALID_LOGICAL_NAME",
            "Document logical name must not be empty",
        )
    return logical


def _raise_ingestion_api_error(exc: IngestionError) -> None:
    if exc.code == IngestionErrorCode.DOCUMENT_TOO_LARGE:
        status_code = 413
    elif exc.code in {
        IngestionErrorCode.UNSUPPORTED_DOCUMENT_FORMAT,
        IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH,
    }:
        status_code = 415
    else:
        status_code = 422
    raise AppError(
        status_code,
        exc.code.value,
        exc.message,
        details=exc.details,
    ) from exc


async def _read_upload(upload: UploadFile, limit: int) -> bytes:
    data = bytearray()
    try:
        while chunk := await upload.read(_READ_SIZE):
            data.extend(chunk)
            if len(data) > limit:
                raise AppError(
                    413,
                    IngestionErrorCode.DOCUMENT_TOO_LARGE.value,
                    "The document exceeds the upload limit",
                    details={"max_bytes": limit},
                )
    finally:
        await upload.close()
    return bytes(data)


def _write_atomic(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=".upload-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


async def _owner_course(
    session: AsyncSession,
    course_id: uuid.UUID,
    teacher: User,
    *,
    for_update: bool = False,
) -> Course:
    statement = select(Course).where(Course.id == course_id)
    if for_update:
        statement = statement.with_for_update()
    course = await session.scalar(statement)
    if course is None:
        raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
    if teacher.role != UserRole.TEACHER or course.owner_id != teacher.id:
        raise AppError(
            403,
            "COURSE_ACCESS_DENIED",
            "You do not have access to this course",
        )
    return course


async def _ensure_course_access(
    session: AsyncSession, course: Course, user: User
) -> None:
    if user.role == UserRole.TEACHER:
        if course.owner_id == user.id:
            return
    elif course.status == CourseStatus.ACTIVE:
        enrollment = await session.scalar(
            select(Enrollment.id).where(
                Enrollment.course_id == course.id,
                Enrollment.student_id == user.id,
                Enrollment.status == EnrollmentStatus.ACTIVE,
            )
        )
        if enrollment is not None:
            return
    raise AppError(
        403,
        "COURSE_ACCESS_DENIED",
        "You do not have access to this course",
    )


def _job_payload(job: IngestionJob | None) -> dict[str, object] | None:
    if job is None:
        return None
    return {
        "id": job.id,
        "version_id": job.version_id,
        "stage": job.stage.value,
        "progress": job.progress,
        "error_code": job.error_code,
        "error_message": job.error_message,
        "retry_count": job.retry_count,
        "stage_details": _public_stage_details(job.stage_details),
        "started_at": job.started_at,
        "finished_at": job.finished_at,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def _public_stage_details(value):
    if isinstance(value, dict):
        return {
            key: _public_stage_details(item)
            for key, item in value.items()
            if key not in {"path", "file_path", "lexical_path"}
        }
    if isinstance(value, list):
        return [_public_stage_details(item) for item in value]
    return value


def _version_payload(
    version: DocumentVersion, job: IngestionJob | None = None
) -> dict[str, object]:
    return {
        "id": version.id,
        "document_id": version.document_id,
        "sha256": version.sha256,
        "version": version.version,
        "original_filename": version.original_filename,
        "media_type": version.media_type,
        "size_bytes": version.size_bytes,
        "status": version.status.value,
        "published_at": version.published_at,
        "created_at": version.created_at,
        "updated_at": version.updated_at,
        "ingestion_job_id": job.id if job is not None else None,
    }


def _document_payload(
    document: Document,
    version: DocumentVersion | None = None,
    job: IngestionJob | None = None,
    *,
    duplicate: bool | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": document.id,
        "course_id": document.course_id,
        "logical_name": document.logical_name,
        "status": document.status.value,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "latest_version": _version_payload(version, job) if version else None,
        "ingestion_job": _job_payload(job),
    }
    if duplicate is not None:
        payload["duplicate"] = duplicate
    return payload


async def _duplicate_for_course(
    session: AsyncSession, course_id: uuid.UUID, sha256: str
) -> tuple[Document, DocumentVersion, IngestionJob | None] | None:
    return (
        await session.execute(
            select(Document, DocumentVersion, IngestionJob)
            .join(
                DocumentVersion,
                DocumentVersion.document_id == Document.id,
            )
            .outerjoin(IngestionJob, IngestionJob.version_id == DocumentVersion.id)
            .where(Document.course_id == course_id, DocumentVersion.sha256 == sha256)
            .order_by(DocumentVersion.created_at.asc())
            .limit(1)
        )
    ).one_or_none()


async def _dispatch(request: Request, job_id: uuid.UUID) -> None:
    dispatcher = getattr(request.app.state, "ingestion_dispatcher", None)
    try:
        if dispatcher is not None:
            result = dispatcher(str(job_id))
            if inspect.isawaitable(result):
                await result
        elif request.app.state.settings.environment != "test":
            await asyncio.to_thread(enqueue_ingestion_job, job_id)
    except Exception:
        logger.exception(
            "Failed to dispatch ingestion job", extra={"job_id": str(job_id)}
        )
        # Persist a retryable, diagnostic state instead of leaving a job stuck
        # in QUEUED forever when the broker is unavailable.
        async with request.app.state.session_factory() as failure_session:
            job = await failure_session.scalar(
                select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
            )
            if job is not None and job.stage == IngestionStage.QUEUED:
                job.stage = IngestionStage.FAILED
                job.error_code = "INGESTION_DISPATCH_FAILED"
                job.error_message = "The ingestion job could not be dispatched"
                job.finished_at = datetime.now(UTC)
                job.stage_details = {
                    **dict(job.stage_details or {}),
                    "failed_from_stage": IngestionStage.QUEUED.value,
                }
                version = await failure_session.get(DocumentVersion, job.version_id)
                if version is not None:
                    version.status = DocumentVersionStatus.FAILED
                await failure_session.commit()


async def _job_resources(
    session: AsyncSession, job_id: uuid.UUID, teacher: User, *, for_update: bool = False
) -> tuple[IngestionJob, DocumentVersion, Document, Course]:
    statement = (
        select(IngestionJob, DocumentVersion, Document, Course)
        .join(DocumentVersion, DocumentVersion.id == IngestionJob.version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .join(Course, Course.id == Document.course_id)
        .where(IngestionJob.id == job_id)
    )
    if for_update:
        statement = statement.with_for_update()
    row = (await session.execute(statement)).one_or_none()
    if row is None:
        raise AppError(404, "INGESTION_JOB_NOT_FOUND", "Ingestion job not found")
    job, version, document, course = row
    if course.owner_id != teacher.id:
        raise AppError(
            403,
            "COURSE_ACCESS_DENIED",
            "You do not have access to this course",
        )
    return job, version, document, course


@router.post("/api/v1/courses/{course_id}/documents", status_code=201)
async def upload_document(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
    file: Annotated[UploadFile, File(...)],
    logical_name: Annotated[str | None, Form()] = None,
):
    course = await _owner_course(session, course_id, teacher, for_update=True)
    if course.status != CourseStatus.ACTIVE:
        raise AppError(409, "COURSE_ARCHIVED", "Archived courses cannot accept uploads")

    original_filename = _safe_original_filename(file.filename)
    name = _logical_name(logical_name, original_filename)
    limit = min(
        int(request.app.state.settings.max_upload_bytes),
        _ABSOLUTE_MAX_UPLOAD_BYTES,
    )
    data = await _read_upload(file, limit)
    try:
        document_format = await asyncio.to_thread(
            detect_document_format, data, original_filename
        )
    except IngestionError as exc:
        _raise_ingestion_api_error(exc)
    digest = hashlib.sha256(data).hexdigest()

    duplicate = await _duplicate_for_course(session, course_id, digest)
    if duplicate is not None:
        document, version, job = duplicate
        return success_response(
            request,
            _document_payload(document, version, job, duplicate=True),
        )

    document = await session.scalar(
        select(Document)
        .where(Document.course_id == course_id, Document.logical_name == name)
        .with_for_update()
    )
    if document is None:
        document = Document(
            course_id=course_id,
            logical_name=name,
            status=DocumentStatus.ACTIVE,
        )
        session.add(document)
        await session.flush()
        next_version = 1
    else:
        document.status = DocumentStatus.ACTIVE
        latest_version = await session.scalar(
            select(func.max(DocumentVersion.version)).where(
                DocumentVersion.document_id == document.id
            )
        )
        next_version = int(latest_version or 0) + 1

    stored_filename = f"{secrets.token_hex(16)}{_FORMAT_EXTENSION[document_format]}"
    target = (
        Path(request.app.state.settings.upload_root)
        / str(course_id)
        / str(document.id)
        / stored_filename
    )
    await asyncio.to_thread(_write_atomic, target, data)
    version = DocumentVersion(
        document_id=document.id,
        sha256=digest,
        version=next_version,
        original_filename=original_filename,
        media_type=_FORMAT_MEDIA_TYPE[document_format],
        file_path=str(target),
        size_bytes=len(data),
        status=DocumentVersionStatus.UPLOADED,
    )
    try:
        session.add(version)
        await session.flush()
        job = IngestionJob(
            version_id=version.id, stage=IngestionStage.QUEUED, progress=0
        )
        session.add(job)
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        await asyncio.to_thread(target.unlink, missing_ok=True)
        duplicate = await _duplicate_for_course(session, course_id, digest)
        if duplicate is None:
            raise AppError(
                409,
                "DOCUMENT_VERSION_CONFLICT",
                "A concurrent upload created this document version",
            ) from exc
        existing_document, existing_version, existing_job = duplicate
        return success_response(
            request,
            _document_payload(
                existing_document,
                existing_version,
                existing_job,
                duplicate=True,
            ),
        )
    except Exception:
        await session.rollback()
        await asyncio.to_thread(target.unlink, missing_ok=True)
        raise

    await _dispatch(request, job.id)
    await session.refresh(job)
    await session.refresh(version)
    return success_response(
        request,
        _document_payload(document, version, job, duplicate=False),
        status_code=201,
    )


@router.get("/api/v1/courses/{course_id}/documents")
async def list_documents(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    await _owner_course(session, course_id, teacher)
    documents = (
        await session.scalars(
            select(Document)
            .where(Document.course_id == course_id)
            .order_by(Document.created_at.desc())
        )
    ).all()
    if not documents:
        return success_response(request, [])

    rows = (
        await session.execute(
            select(DocumentVersion, IngestionJob)
            .outerjoin(IngestionJob, IngestionJob.version_id == DocumentVersion.id)
            .where(DocumentVersion.document_id.in_([item.id for item in documents]))
            .order_by(
                DocumentVersion.document_id,
                DocumentVersion.version.desc(),
            )
        )
    ).all()
    latest: dict[uuid.UUID, tuple[DocumentVersion, IngestionJob | None]] = {}
    for version, job in rows:
        latest.setdefault(version.document_id, (version, job))
    return success_response(
        request,
        [
            _document_payload(document, *latest[document.id])
            if document.id in latest
            else _document_payload(document)
            for document in documents
        ],
    )


@router.get("/api/v1/documents/{document_id}/versions")
async def list_document_versions(
    document_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    row = (
        await session.execute(
            select(Document, Course)
            .join(Course, Course.id == Document.course_id)
            .where(Document.id == document_id)
        )
    ).one_or_none()
    if row is None:
        raise AppError(404, "DOCUMENT_NOT_FOUND", "Document not found")
    document, course = row
    if course.owner_id != teacher.id:
        raise AppError(
            403,
            "COURSE_ACCESS_DENIED",
            "You do not have access to this course",
        )
    rows = (
        await session.execute(
            select(DocumentVersion, IngestionJob)
            .outerjoin(IngestionJob, IngestionJob.version_id == DocumentVersion.id)
            .where(DocumentVersion.document_id == document.id)
            .order_by(DocumentVersion.version.desc())
        )
    ).all()
    return success_response(
        request,
        [
            {**_version_payload(version, job), "ingestion_job": _job_payload(job)}
            for version, job in rows
        ],
    )


@router.get("/api/v1/ingestion-jobs/{job_id}")
async def get_ingestion_job(
    job_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    job, _version, _document, _course = await _job_resources(session, job_id, teacher)
    return success_response(request, _job_payload(job))


@router.post("/api/v1/ingestion-jobs/{job_id}/retry")
async def retry_ingestion_job(
    job_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    job, version, _document, _course = await _job_resources(
        session, job_id, teacher, for_update=True
    )
    if job.stage != IngestionStage.FAILED:
        raise AppError(
            409,
            "INGESTION_JOB_NOT_RETRYABLE",
            "Only failed ingestion jobs can be retried",
            details={"stage": job.stage.value},
        )
    job.stage = IngestionStage.QUEUED
    job.progress = 0
    job.error_code = None
    job.error_message = None
    job.retry_count += 1
    job.started_at = None
    job.finished_at = None
    job.stage_details = {
        **dict(job.stage_details or {}),
        "retry_requested_at": datetime.now(UTC).isoformat(),
    }
    version.status = DocumentVersionStatus.UPLOADED
    await session.commit()
    await _dispatch(request, job.id)
    await session.refresh(job)
    await session.refresh(version)
    return success_response(request, _job_payload(job))


@router.post("/api/v1/ingestion-jobs/{job_id}/cancel")
async def cancel_ingestion_job(
    job_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    job, version, _document, _course = await _job_resources(
        session, job_id, teacher, for_update=True
    )
    if job.stage == IngestionStage.CANCELLED:
        return success_response(request, _job_payload(job))
    if job.stage in {
        IngestionStage.READY_FOR_REVIEW,
        IngestionStage.PUBLISHED,
        IngestionStage.FAILED,
    }:
        raise AppError(
            409,
            "INGESTION_JOB_NOT_CANCELLABLE",
            "The ingestion job is already in a terminal stage",
            details={"stage": job.stage.value},
        )
    job.stage = IngestionStage.CANCELLED
    job.finished_at = datetime.now(UTC)
    job.stage_details = {
        **dict(job.stage_details or {}),
        "cancelled_at": job.finished_at.isoformat(),
    }
    version.status = DocumentVersionStatus.UPLOADED
    await session.commit()
    return success_response(request, _job_payload(job))


@router.get("/api/v1/chunks/{chunk_id}")
async def get_chunk(
    chunk_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
):
    row = (
        await session.execute(
            select(Chunk, DocumentVersion, Document, Course)
            .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .join(Course, Course.id == Document.course_id)
            .where(Chunk.id == chunk_id)
        )
    ).one_or_none()
    if row is None:
        raise AppError(404, "CHUNK_NOT_FOUND", "Chunk not found")
    chunk, version, document, course = row
    await _ensure_course_access(session, course, current_user)
    if current_user.role == UserRole.STUDENT and (
        document.status != DocumentStatus.ACTIVE
        or document.deleted_at is not None
        or version.status != DocumentVersionStatus.PUBLISHED
        or version.deleted_at is not None
        or chunk.status != RecordStatus.ACTIVE
        or chunk.deleted_at is not None
    ):
        raise AppError(
            403,
            "COURSE_ACCESS_DENIED",
            "You do not have access to this course resource",
        )
    return success_response(
        request,
        {
            "id": chunk.id,
            "course_id": course.id,
            "document_id": document.id,
            "document_name": document.logical_name,
            "version_id": version.id,
            "version": version.version,
            "original_filename": version.original_filename,
            "content": chunk.content,
            "page": chunk.page,
            "section_path": chunk.section_path,
            "title_level": chunk.title_level,
            "chunk_type": chunk.chunk_type.value,
            "previous_chunk_id": chunk.previous_chunk_id,
            "next_chunk_id": chunk.next_chunk_id,
        },
    )
