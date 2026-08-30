from __future__ import annotations

import uuid

from sqlalchemy import select

from app.ingestion.pipeline import IngestionPipeline
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
    CourseTemplate,
    Document,
    DocumentVersion,
    IngestionJob,
    RelationCandidate,
    RelationType,
    ReviewStatus,
    User,
    UserRole,
)


class _UnusedEmbeddingAdapter:
    async def embed_documents(self, texts):
        raise AssertionError(f"embedding should not run for {len(texts)} texts")


def _heading(
    name: str,
    path: tuple[str, ...],
    *,
    candidate_id: uuid.UUID | None = None,
    chunk_id: uuid.UUID | None = None,
) -> HeadingCandidateSignal:
    return HeadingCandidateSignal(
        candidate_id=candidate_id or uuid.uuid4(),
        name=name,
        section_path=path,
        source_chunk_id=chunk_id or uuid.uuid4(),
    )


def test_explicit_prerequisite_requires_unique_same_index_name_resolution():
    stack = _heading("Stacks", ("Stacks",))
    first_array = _heading("Arrays", ("Arrays",))
    second_array = _heading("arrays", ("Alternate arrays",))
    chunk = ChunkRelationSignal(
        chunk_id=stack.source_chunk_id,
        ordinal=1,
        content="Prerequisite: ARRAYS. Stacks are linear structures.",
        section_path=stack.section_path,
    )

    ambiguous = extract_explicit_relation_candidates(
        [chunk], [stack, first_array, second_array]
    )
    assert ambiguous == []

    resolved = extract_explicit_relation_candidates([chunk], [stack, first_array])
    assert len(resolved) == 1
    relation = resolved[0]
    assert relation.relation_type is RelationType.PREREQUISITE_OF
    assert relation.from_candidate_id == first_array.candidate_id
    assert relation.to_candidate_id == stack.candidate_id
    assert relation.source_chunk_id == chunk.chunk_id
    assert relation.evidence == "Prerequisite: ARRAYS"
    assert relation.confidence == 1.0
    assert relation.extraction_model == "deterministic-document-signals-v1"


def test_order_and_negated_marker_do_not_create_prerequisites():
    arrays = _heading("数组", ("数组",))
    stack = _heading("栈", ("栈",))
    chunks = [
        ChunkRelationSignal(
            chunk_id=arrays.source_chunk_id,
            ordinal=0,
            content="数组使用连续存储。",
            section_path=arrays.section_path,
        ),
        ChunkRelationSignal(
            chunk_id=stack.source_chunk_id,
            ordinal=1,
            content="无需前置知识：数组。栈遵循后进先出。",
            section_path=stack.section_path,
        ),
    ]

    assert extract_explicit_relation_candidates(chunks, [arrays, stack]) == []


async def test_pipeline_persists_evidence_backed_relations_idempotently(app_instance):
    async with app_instance.state.session_factory() as session:
        owner = User(
            email="relation-extraction@example.com",
            password_hash="unused",
            role=UserRole.TEACHER,
        )
        session.add(owner)
        await session.flush()
        course = Course(
            owner_id=owner.id,
            template=CourseTemplate.DATA_STRUCTURES,
            code="DS-REL",
            name="Data Structures",
            description="",
            semester="2026-Fall",
        )
        session.add(course)
        await session.flush()
        document = Document(course_id=course.id, logical_name="relations")
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            document_id=document.id,
            sha256="a" * 64,
            version=1,
            original_filename="relations.md",
            media_type="text/markdown",
            file_path="unused.md",
            size_bytes=1,
        )
        session.add(version)
        await session.flush()
        course_index = CourseIndex(course_id=course.id, version=1)
        job = IngestionJob(version_id=version.id)
        session.add_all((course_index, job))
        await session.flush()

        chunks = [
            Chunk(
                version_id=version.id,
                ordinal=0,
                content="课程介绍。",
                page=1,
                section_path=["数据结构"],
                title_level=1,
                char_count=5,
            ),
            Chunk(
                version_id=version.id,
                ordinal=1,
                content="数组使用连续存储。",
                page=1,
                section_path=["数据结构", "数组"],
                title_level=2,
                char_count=9,
            ),
            Chunk(
                version_id=version.id,
                ordinal=2,
                content="前置知识：数组。栈遵循后进先出。",
                page=2,
                section_path=["数据结构", "栈"],
                title_level=2,
                char_count=16,
            ),
        ]
        session.add_all(chunks)
        await session.commit()
        job_id = job.id
        course_id = course.id
        version_id = version.id
        index_id = course_index.id

    pipeline = IngestionPipeline(
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        embedding_adapter=_UnusedEmbeddingAdapter(),
    )
    await pipeline._extract_heading_candidates(
        job_id=job_id,
        course_id=course_id,
        version_id=version_id,
        course_index_id=index_id,
    )
    await pipeline._extract_heading_candidates(
        job_id=job_id,
        course_id=course_id,
        version_id=version_id,
        course_index_id=index_id,
    )

    async with app_instance.state.session_factory() as session:
        concepts = (
            await session.scalars(
                select(ConceptCandidate).where(ConceptCandidate.index_id == index_id)
            )
        ).all()
        relations = (
            await session.scalars(
                select(RelationCandidate).where(
                    RelationCandidate.course_id == course_id
                )
            )
        ).all()
        refreshed_job = await session.get(IngestionJob, job_id)

    assert len(concepts) == 3
    assert len(relations) == 3
    assert all(item.status is ReviewStatus.PENDING for item in relations)
    assert all(item.confidence == 1.0 for item in relations)
    assert all(
        item.extraction_model == "deterministic-document-signals-v1"
        for item in relations
    )

    names = {item.id: item.name for item in concepts}
    edges = {
        (names[item.from_candidate_id], item.type, names[item.to_candidate_id]): item
        for item in relations
    }
    assert ("数组", RelationType.PART_OF, "数据结构") in edges
    assert ("栈", RelationType.PART_OF, "数据结构") in edges
    prerequisite = edges[("数组", RelationType.PREREQUISITE_OF, "栈")]
    assert prerequisite.evidence == "前置知识：数组"
    assert refreshed_job is not None
    assert refreshed_job.stage_details["concept_candidates"]["created_count"] == 0
    assert refreshed_job.stage_details["relation_candidates"]["created_count"] == 0
