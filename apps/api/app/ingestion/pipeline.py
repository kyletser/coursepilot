from __future__ import annotations

import asyncio
import math
import uuid
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.ingestion.chunking import chunk_document
from app.ingestion.errors import IngestionError, IngestionErrorCode
from app.ingestion.parsers import parse_document
from app.ingestion.relations import (
    ChunkRelationSignal,
    HeadingCandidateSignal,
    extract_explicit_relation_candidates,
)
from app.models import (
    Chunk,
    ConceptCandidate,
    Course,
    CourseIndex,
    CourseIndexStatus,
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    IndexComponentStatus,
    IngestionJob,
    IngestionStage,
    RecordStatus,
    RelationCandidate,
    ReviewStatus,
    utc_now,
)
from app.models import (
    ChunkType as PersistedChunkType,
)
from app.retrieval import (
    BGEM3EmbeddingAdapter,
    LexicalDocument,
    LexicalIndexArtifactStore,
    LexicalIndexManager,
    ModelDependencyUnavailableError,
)

SessionFactory = async_sessionmaker[AsyncSession]


class DocumentEmbeddingAdapter(Protocol):
    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...


class PipelineCancelled(Exception):
    pass


_CHECKPOINT_ORDER = {
    IngestionStage.CHUNKING.value: 1,
    IngestionStage.EMBEDDING.value: 2,
    IngestionStage.LEXICAL_INDEXING.value: 3,
    IngestionStage.GRAPH_EXTRACTING.value: 4,
    IngestionStage.VALIDATING.value: 5,
}


def _has_checkpoint(details: dict[str, object], stage: IngestionStage) -> bool:
    checkpoint = details.get("last_safe_stage")
    if not isinstance(checkpoint, str):
        return False
    return _CHECKPOINT_ORDER.get(checkpoint, 0) >= _CHECKPOINT_ORDER[stage.value]


def _embedding_text(chunk: Chunk) -> str:
    section_path = [str(item) for item in (chunk.section_path or []) if str(item)]
    if not section_path:
        return chunk.content
    return f"{' > '.join(section_path)}\n\n{chunk.content}"


class IngestionPipeline:
    """Idempotent database-backed ingestion orchestration.

    Each durable phase records ``last_safe_stage`` on the job. A retry reuses the
    same DocumentVersion and CourseIndex and starts after that checkpoint. The
    ACTIVE course index is never modified by this pipeline.
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        settings: Settings,
        embedding_adapter: DocumentEmbeddingAdapter | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.embedding_adapter = embedding_adapter or BGEM3EmbeddingAdapter(
            settings.embedding_model,
            allow_download=settings.model_allow_download,
            cache_folder=settings.hf_home,
        )

    async def run(self, job_id: uuid.UUID) -> dict[str, object]:
        try:
            context = await self._prepare(job_id)
            if context["terminal"]:
                return {
                    "job_id": str(job_id),
                    "stage": str(context["stage"]),
                    "course_index_id": context.get("course_index_id"),
                }

            version_id = uuid.UUID(str(context["version_id"]))
            document_id = uuid.UUID(str(context["document_id"]))
            course_id = uuid.UUID(str(context["course_id"]))
            file_path = Path(str(context["file_path"]))
            original_filename = str(context["original_filename"])
            details = dict(context["stage_details"])

            if not _has_checkpoint(details, IngestionStage.CHUNKING):
                await self._set_stage(job_id, IngestionStage.PARSING, 10)
                try:
                    data = await asyncio.to_thread(file_path.read_bytes)
                except OSError as exc:
                    raise IngestionError(
                        IngestionErrorCode.DOCUMENT_CORRUPTED,
                        message="The stored document is unavailable",
                        details={"reason": type(exc).__name__},
                    ) from exc
                parsed = await asyncio.to_thread(
                    parse_document, data, original_filename
                )

                await self._set_stage(job_id, IngestionStage.CHUNKING, 25)
                drafts = await asyncio.to_thread(
                    chunk_document,
                    parsed,
                    document_key=str(version_id),
                )
                await self._persist_chunks(job_id, version_id, drafts)
                details["last_safe_stage"] = IngestionStage.CHUNKING.value

            course_index = await self._get_or_create_course_index(job_id, course_id)
            details = await self._job_details(job_id)

            if not _has_checkpoint(details, IngestionStage.EMBEDDING):
                await self._embed_chunks(job_id, version_id, course_index.id)
                details = await self._job_details(job_id)

            if not _has_checkpoint(details, IngestionStage.LEXICAL_INDEXING):
                await self._build_lexical_index(
                    job_id=job_id,
                    course_id=course_id,
                    document_id=document_id,
                    version_id=version_id,
                    course_index_id=course_index.id,
                    index_version=course_index.version,
                )
                details = await self._job_details(job_id)

            if not _has_checkpoint(details, IngestionStage.GRAPH_EXTRACTING):
                await self._extract_heading_candidates(
                    job_id=job_id,
                    course_id=course_id,
                    version_id=version_id,
                    course_index_id=course_index.id,
                )

            await self._validate_and_finish(
                job_id=job_id,
                version_id=version_id,
                course_index_id=course_index.id,
                course_id=course_id,
                index_version=course_index.version,
            )
            return {
                "job_id": str(job_id),
                "stage": IngestionStage.READY_FOR_REVIEW.value,
                "course_index_id": str(course_index.id),
                "course_index_version": course_index.version,
            }
        except PipelineCancelled:
            return {"job_id": str(job_id), "stage": IngestionStage.CANCELLED.value}
        except Exception as exc:
            await self._mark_failed(job_id, exc)
            raise

    async def _prepare(self, job_id: uuid.UUID) -> dict[str, object]:
        async with self.session_factory() as session:
            row = (
                await session.execute(
                    select(IngestionJob, DocumentVersion, Document)
                    .join(
                        DocumentVersion,
                        DocumentVersion.id == IngestionJob.version_id,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(IngestionJob.id == job_id)
                    .with_for_update()
                )
            ).one_or_none()
            if row is None:
                raise LookupError(f"Ingestion job {job_id} was not found")
            job, version, document = row
            details = dict(job.stage_details or {})

            if job.stage == IngestionStage.CANCELLED:
                return {
                    "terminal": True,
                    "stage": job.stage.value,
                    "course_index_id": details.get("course_index_id"),
                }
            if job.stage in {
                IngestionStage.READY_FOR_REVIEW,
                IngestionStage.PUBLISHED,
            }:
                return {
                    "terminal": True,
                    "stage": job.stage.value,
                    "course_index_id": details.get("course_index_id"),
                }

            now = utc_now()
            if job.started_at is None:
                job.started_at = now
            job.finished_at = None
            job.error_code = None
            job.error_message = None
            version.status = DocumentVersionStatus.PROCESSING

            index_id = details.get("course_index_id")
            if isinstance(index_id, str):
                try:
                    course_index = await session.get(CourseIndex, uuid.UUID(index_id))
                except ValueError:
                    course_index = None
                if course_index is not None:
                    course_index.status = CourseIndexStatus.BUILDING
            await session.commit()
            return {
                "terminal": False,
                "stage": job.stage.value,
                "version_id": str(version.id),
                "document_id": str(document.id),
                "course_id": str(document.course_id),
                "file_path": version.file_path,
                "original_filename": version.original_filename,
                "stage_details": details,
            }

    async def _job_details(self, job_id: uuid.UUID) -> dict[str, object]:
        async with self.session_factory() as session:
            details = await session.scalar(
                select(IngestionJob.stage_details).where(IngestionJob.id == job_id)
            )
            return dict(details or {})

    async def _locked_job(
        self, session: AsyncSession, job_id: uuid.UUID
    ) -> IngestionJob:
        job = await session.scalar(
            select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
        )
        if job is None:
            raise LookupError(f"Ingestion job {job_id} was not found")
        if job.stage == IngestionStage.CANCELLED:
            raise PipelineCancelled
        return job

    async def _set_stage(
        self,
        job_id: uuid.UUID,
        stage: IngestionStage,
        progress: int,
        *,
        details: dict[str, object] | None = None,
    ) -> None:
        async with self.session_factory() as session:
            job = await self._locked_job(session, job_id)
            job.stage = stage
            job.progress = progress
            if details:
                job.stage_details = {**dict(job.stage_details or {}), **details}
            await session.commit()

    async def _persist_chunks(
        self,
        job_id: uuid.UUID,
        version_id: uuid.UUID,
        drafts,
    ) -> None:
        if not drafts:
            raise IngestionError(IngestionErrorCode.DOCUMENT_EMPTY)
        async with self.session_factory() as session:
            job = await self._locked_job(session, job_id)
            existing = (
                await session.scalars(
                    select(Chunk).where(Chunk.version_id == version_id)
                )
            ).all()
            for chunk in existing:
                await session.delete(chunk)
            await session.flush()

            chunks = [
                Chunk(
                    id=draft.chunk_id,
                    version_id=version_id,
                    ordinal=draft.ordinal,
                    content=draft.content,
                    page=draft.page,
                    section_path=list(draft.section_path),
                    title_level=draft.title_level,
                    chunk_type=PersistedChunkType(draft.chunk_type.value),
                    char_count=draft.char_count,
                    status=RecordStatus.ACTIVE,
                )
                for draft in drafts
            ]
            session.add_all(chunks)
            # Insert stable IDs first so SQLite and PostgreSQL can both validate
            # the self-referential adjacency foreign keys on the second flush.
            await session.flush()
            for chunk, draft in zip(chunks, drafts, strict=True):
                chunk.previous_chunk_id = draft.previous_chunk_id
                chunk.next_chunk_id = draft.next_chunk_id

            job.stage_details = {
                **dict(job.stage_details or {}),
                "last_safe_stage": IngestionStage.CHUNKING.value,
                "chunk_count": len(chunks),
            }
            await session.commit()

    async def _get_or_create_course_index(
        self, job_id: uuid.UUID, course_id: uuid.UUID
    ) -> CourseIndex:
        async with self.session_factory() as session:
            job = await self._locked_job(session, job_id)
            details = dict(job.stage_details or {})
            raw_index_id = details.get("course_index_id")
            if isinstance(raw_index_id, str):
                try:
                    existing = await session.get(CourseIndex, uuid.UUID(raw_index_id))
                except ValueError:
                    existing = None
                if existing is not None and existing.course_id == course_id:
                    existing.status = CourseIndexStatus.BUILDING
                    await session.commit()
                    return existing

            # Serialize version allocation with the owning course row.
            course = await session.scalar(
                select(Course).where(Course.id == course_id).with_for_update()
            )
            if course is None:
                raise LookupError(f"Course {course_id} was not found")
            latest = await session.scalar(
                select(func.max(CourseIndex.version)).where(
                    CourseIndex.course_id == course_id
                )
            )
            course_index = CourseIndex(
                course_id=course_id,
                version=int(latest or 0) + 1,
                status=CourseIndexStatus.BUILDING,
            )
            session.add(course_index)
            await session.flush()
            job.stage_details = {
                **details,
                "course_index_id": str(course_index.id),
                "course_index_version": course_index.version,
            }
            await session.commit()
            return course_index

    async def _embed_chunks(
        self,
        job_id: uuid.UUID,
        version_id: uuid.UUID,
        course_index_id: uuid.UUID,
    ) -> None:
        await self._set_stage(job_id, IngestionStage.EMBEDDING, 45)
        async with self.session_factory() as session:
            course_index = await session.get(CourseIndex, course_index_id)
            if course_index is None:
                raise LookupError(f"Course index {course_index_id} was not found")
            course_index.dense_status = IndexComponentStatus.BUILDING
            await session.commit()

        async with self.session_factory() as session:
            chunks = (
                await session.scalars(
                    select(Chunk)
                    .where(
                        Chunk.version_id == version_id,
                        Chunk.status == RecordStatus.ACTIVE,
                    )
                    .order_by(Chunk.ordinal)
                )
            ).all()
        texts = [_embedding_text(chunk) for chunk in chunks]

        try:
            vectors = await self.embedding_adapter.embed_documents(texts)
            if len(vectors) != len(chunks):
                raise ValueError("embedding model returned an unexpected vector count")
            for vector in vectors:
                if len(vector) != 1024 or any(
                    not math.isfinite(float(value)) for value in vector
                ):
                    raise ValueError(
                        "embedding model returned an invalid BGE-M3 vector"
                    )
        except (ModelDependencyUnavailableError, ImportError, OSError) as exc:
            async with self.session_factory() as session:
                job = await self._locked_job(session, job_id)
                course_index = await session.get(CourseIndex, course_index_id)
                if course_index is None:
                    raise LookupError(f"Course index {course_index_id} was not found")
                course_index.dense_status = IndexComponentStatus.PENDING
                job.stage_details = {
                    **dict(job.stage_details or {}),
                    "last_safe_stage": IngestionStage.EMBEDDING.value,
                    "embedding": {
                        "status": IndexComponentStatus.PENDING.value,
                        "reason": "EMBEDDING_MODEL_UNAVAILABLE",
                        "exception": type(exc).__name__,
                    },
                }
                await session.commit()
            return

        async with self.session_factory() as session:
            job = await self._locked_job(session, job_id)
            persisted = (
                await session.scalars(
                    select(Chunk)
                    .where(Chunk.version_id == version_id)
                    .order_by(Chunk.ordinal)
                )
            ).all()
            if [chunk.id for chunk in persisted] != [chunk.id for chunk in chunks]:
                raise RuntimeError("chunk set changed while embeddings were generated")
            for chunk, vector in zip(persisted, vectors, strict=True):
                chunk.embedding = [float(value) for value in vector]
            course_index = await session.get(CourseIndex, course_index_id)
            if course_index is None:
                raise LookupError(f"Course index {course_index_id} was not found")
            course_index.dense_status = IndexComponentStatus.READY
            job.stage_details = {
                **dict(job.stage_details or {}),
                "last_safe_stage": IngestionStage.EMBEDDING.value,
                "embedding": {
                    "status": IndexComponentStatus.READY.value,
                    "model": self.settings.embedding_model,
                    "dimension": 1024,
                },
            }
            await session.commit()

    async def _lexical_documents(
        self,
        *,
        course_id: uuid.UUID,
        current_document_id: uuid.UUID,
        current_version_id: uuid.UUID,
    ) -> list[LexicalDocument]:
        async with self.session_factory() as session:
            rows = (
                await session.execute(
                    select(Chunk, DocumentVersion, Document)
                    .join(
                        DocumentVersion,
                        DocumentVersion.id == Chunk.version_id,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        Document.course_id == course_id,
                        Document.status == DocumentStatus.ACTIVE,
                        Document.deleted_at.is_(None),
                        Chunk.status == RecordStatus.ACTIVE,
                        Chunk.deleted_at.is_(None),
                        DocumentVersion.deleted_at.is_(None),
                        or_(
                            DocumentVersion.id == current_version_id,
                            DocumentVersion.status.in_(
                                (
                                    DocumentVersionStatus.READY_FOR_REVIEW,
                                    DocumentVersionStatus.PUBLISHED,
                                )
                            ),
                        ),
                    )
                )
            ).all()

        selected_versions: dict[uuid.UUID, tuple[int, uuid.UUID]] = {}
        for _chunk, version, document in rows:
            candidate = (version.version, version.id)
            if document.id == current_document_id:
                if version.id == current_version_id:
                    selected_versions[document.id] = candidate
                continue
            if candidate > selected_versions.get(document.id, (0, uuid.UUID(int=0))):
                selected_versions[document.id] = candidate

        selected_ids = {value[1] for value in selected_versions.values()}
        documents = [
            LexicalDocument(
                chunk_id=str(chunk.id),
                content=chunk.content,
                metadata={
                    "course_id": str(course_id),
                    "document_id": str(document.id),
                    "document_name": document.logical_name,
                    "version_id": str(version.id),
                    "version": version.version,
                    "page": chunk.page,
                    "section_path": list(chunk.section_path or []),
                    "chunk_type": chunk.chunk_type.value,
                },
            )
            for chunk, version, document in sorted(
                rows,
                key=lambda item: (
                    item[2].logical_name.casefold(),
                    item[1].version,
                    item[0].ordinal,
                ),
            )
            if version.id in selected_ids
        ]
        if not documents:
            raise IngestionError(IngestionErrorCode.DOCUMENT_EMPTY)
        return documents

    async def _build_lexical_index(
        self,
        *,
        job_id: uuid.UUID,
        course_id: uuid.UUID,
        document_id: uuid.UUID,
        version_id: uuid.UUID,
        course_index_id: uuid.UUID,
        index_version: int,
    ) -> None:
        await self._set_stage(job_id, IngestionStage.LEXICAL_INDEXING, 65)
        async with self.session_factory() as session:
            course_index = await session.get(CourseIndex, course_index_id)
            if course_index is None:
                raise LookupError(f"Course index {course_index_id} was not found")
            course_index.lexical_status = IndexComponentStatus.BUILDING
            await session.commit()

        lexical_documents = await self._lexical_documents(
            course_id=course_id,
            current_document_id=document_id,
            current_version_id=version_id,
        )
        covered_version_ids: list[str] = []
        for document in lexical_documents:
            version_id_value = str(document.metadata.get("version_id", ""))
            if version_id_value and version_id_value not in covered_version_ids:
                covered_version_ids.append(version_id_value)
        manager = LexicalIndexManager(self.settings.index_root)
        artifact_path = await asyncio.to_thread(
            manager.publish,
            str(course_id),
            str(index_version),
            lexical_documents,
        )

        async with self.session_factory() as session:
            job = await self._locked_job(session, job_id)
            course_index = await session.get(CourseIndex, course_index_id)
            if course_index is None:
                raise LookupError(f"Course index {course_index_id} was not found")
            course_index.lexical_status = IndexComponentStatus.READY
            course_index.lexical_path = str(artifact_path)
            course_index.covered_document_version_ids = covered_version_ids
            job.stage_details = {
                **dict(job.stage_details or {}),
                "last_safe_stage": IngestionStage.LEXICAL_INDEXING.value,
                "lexical": {
                    "status": IndexComponentStatus.READY.value,
                    "path": str(artifact_path),
                    "document_count": len(lexical_documents),
                    "covered_document_version_count": len(covered_version_ids),
                },
            }
            await session.commit()

    async def _extract_heading_candidates(
        self,
        *,
        job_id: uuid.UUID,
        course_id: uuid.UUID,
        version_id: uuid.UUID,
        course_index_id: uuid.UUID,
    ) -> None:
        await self._set_stage(job_id, IngestionStage.GRAPH_EXTRACTING, 80)
        async with self.session_factory() as session:
            job = await self._locked_job(session, job_id)
            chunks = (
                await session.scalars(
                    select(Chunk)
                    .where(
                        Chunk.version_id == version_id,
                        Chunk.status == RecordStatus.ACTIVE,
                    )
                    .order_by(Chunk.ordinal)
                )
            ).all()

            existing_candidate_rows = (
                await session.execute(
                    select(ConceptCandidate, Chunk)
                    .join(Chunk, Chunk.id == ConceptCandidate.source_chunk_id)
                    .where(
                        ConceptCandidate.course_id == course_id,
                        ConceptCandidate.index_id == course_index_id,
                        ConceptCandidate.deleted_at.is_(None),
                        Chunk.deleted_at.is_(None),
                    )
                )
            ).all()
            existing_candidate_keys = {
                (candidate.source_chunk_id, candidate.name.casefold())
                for candidate, _source_chunk in existing_candidate_rows
            }
            seen_paths: set[tuple[str, ...]] = set()
            candidates: list[ConceptCandidate] = []
            for chunk in chunks:
                section_path = tuple(
                    str(item).strip()
                    for item in (chunk.section_path or [])
                    if str(item).strip()
                )
                if not section_path or section_path in seen_paths:
                    continue
                seen_paths.add(section_path)
                name = section_path[-1][:255]
                evidence = chunk.content.strip()[:1000]
                if not name or not evidence:
                    continue
                candidate_key = (chunk.id, name.casefold())
                if candidate_key in existing_candidate_keys:
                    continue
                existing_candidate_keys.add(candidate_key)
                candidates.append(
                    ConceptCandidate(
                        course_id=course_id,
                        index_id=course_index_id,
                        name=name,
                        description=chunk.content.strip()[:500],
                        aliases=[],
                        source_chunk_id=chunk.id,
                        evidence=evidence,
                        extraction_model="deterministic-heading-v1",
                        confidence=0.75,
                        status=ReviewStatus.PENDING,
                    )
                )
            session.add_all(candidates)
            await session.flush()

            heading_rows = (
                await session.execute(
                    select(ConceptCandidate, Chunk)
                    .join(Chunk, Chunk.id == ConceptCandidate.source_chunk_id)
                    .where(
                        ConceptCandidate.course_id == course_id,
                        ConceptCandidate.index_id == course_index_id,
                        ConceptCandidate.deleted_at.is_(None),
                        Chunk.deleted_at.is_(None),
                    )
                )
            ).all()
            heading_signals = [
                HeadingCandidateSignal(
                    candidate_id=candidate.id,
                    name=candidate.name,
                    section_path=tuple(str(item) for item in source.section_path or []),
                    source_chunk_id=source.id,
                )
                for candidate, source in heading_rows
            ]
            chunk_signals = [
                ChunkRelationSignal(
                    chunk_id=chunk.id,
                    ordinal=chunk.ordinal,
                    content=chunk.content,
                    section_path=tuple(str(item) for item in chunk.section_path or []),
                )
                for chunk in chunks
            ]
            relation_drafts = extract_explicit_relation_candidates(
                chunk_signals, heading_signals
            )
            existing_relation_keys = set(
                (
                    await session.execute(
                        select(
                            RelationCandidate.from_candidate_id,
                            RelationCandidate.to_candidate_id,
                            RelationCandidate.type,
                            RelationCandidate.source_chunk_id,
                        ).where(RelationCandidate.course_id == course_id)
                    )
                ).all()
            )
            relations = [
                RelationCandidate(
                    course_id=course_id,
                    from_candidate_id=draft.from_candidate_id,
                    to_candidate_id=draft.to_candidate_id,
                    type=draft.relation_type,
                    source_chunk_id=draft.source_chunk_id,
                    evidence=draft.evidence,
                    extraction_model=draft.extraction_model,
                    confidence=draft.confidence,
                    status=ReviewStatus.PENDING,
                )
                for draft in relation_drafts
                if draft.identity not in existing_relation_keys
            ]
            session.add_all(relations)
            job.stage_details = {
                **dict(job.stage_details or {}),
                "last_safe_stage": IngestionStage.GRAPH_EXTRACTING.value,
                "concept_candidates": {
                    "status": ReviewStatus.PENDING.value,
                    "count": len(heading_signals),
                    "created_count": len(candidates),
                    "extractor": "deterministic-heading-v1",
                },
                "relation_candidates": {
                    "status": ReviewStatus.PENDING.value,
                    "count": len(relation_drafts),
                    "created_count": len(relations),
                    "extractor": "deterministic-document-signals-v1",
                },
            }
            await session.commit()

    async def _validate_and_finish(
        self,
        *,
        job_id: uuid.UUID,
        version_id: uuid.UUID,
        course_index_id: uuid.UUID,
        course_id: uuid.UUID,
        index_version: int,
    ) -> None:
        await self._set_stage(job_id, IngestionStage.VALIDATING, 92)
        artifact = await asyncio.to_thread(
            LexicalIndexArtifactStore(self.settings.index_root).load,
            str(course_id),
            str(index_version),
        )
        if not artifact.documents:
            raise RuntimeError("lexical index smoke test found no documents")

        async with self.session_factory() as session:
            job = await self._locked_job(session, job_id)
            version = await session.get(DocumentVersion, version_id)
            course_index = await session.get(CourseIndex, course_index_id)
            if version is None or course_index is None:
                raise LookupError("Ingestion resources disappeared during validation")
            chunk_count = await session.scalar(
                select(func.count(Chunk.id)).where(
                    Chunk.version_id == version_id,
                    Chunk.status == RecordStatus.ACTIVE,
                )
            )
            if not chunk_count:
                raise RuntimeError("chunk smoke test found no chunks")

            now = utc_now()
            course_index.status = CourseIndexStatus.READY
            course_index.smoke_test_result = {
                "ok": True,
                "chunk_count": int(chunk_count),
                "lexical_document_count": len(artifact.documents),
                "dense_status": course_index.dense_status.value,
            }
            course_index.validated_at = now
            version.status = DocumentVersionStatus.READY_FOR_REVIEW
            job.stage = IngestionStage.READY_FOR_REVIEW
            job.progress = 100
            job.error_code = None
            job.error_message = None
            job.finished_at = now
            job.stage_details = {
                **dict(job.stage_details or {}),
                "last_safe_stage": IngestionStage.VALIDATING.value,
                "validation": dict(course_index.smoke_test_result),
            }
            await session.commit()

    async def _mark_failed(self, job_id: uuid.UUID, exc: Exception) -> None:
        if isinstance(exc, PipelineCancelled):
            return
        if isinstance(exc, IngestionError):
            code = exc.code.value
            message = exc.message
        else:
            code = "INGESTION_PIPELINE_FAILED"
            message = "The ingestion pipeline failed"

        async with self.session_factory() as session:
            job = await session.scalar(
                select(IngestionJob).where(IngestionJob.id == job_id).with_for_update()
            )
            if job is None or job.stage == IngestionStage.CANCELLED:
                return
            failed_from = job.stage.value
            job.stage = IngestionStage.FAILED
            job.error_code = code
            job.error_message = message
            job.finished_at = utc_now()
            details = {
                **dict(job.stage_details or {}),
                "failed_from_stage": failed_from,
                "failure_type": type(exc).__name__,
            }
            job.stage_details = details
            version = await session.get(DocumentVersion, job.version_id)
            if version is not None:
                version.status = DocumentVersionStatus.FAILED
            raw_index_id = details.get("course_index_id")
            if isinstance(raw_index_id, str):
                try:
                    course_index = await session.get(
                        CourseIndex, uuid.UUID(raw_index_id)
                    )
                except ValueError:
                    course_index = None
                if course_index is not None:
                    course_index.status = CourseIndexStatus.FAILED
                    if failed_from == IngestionStage.LEXICAL_INDEXING.value:
                        course_index.lexical_status = IndexComponentStatus.FAILED
            await session.commit()


async def run_ingestion_pipeline(
    job_id: uuid.UUID | str,
    *,
    session_factory: SessionFactory,
    settings: Settings,
    embedding_adapter: DocumentEmbeddingAdapter | None = None,
) -> dict[str, object]:
    return await IngestionPipeline(
        session_factory=session_factory,
        settings=settings,
        embedding_adapter=embedding_adapter,
    ).run(uuid.UUID(str(job_id)))
