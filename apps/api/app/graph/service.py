from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.graph.domain import (
    ApprovedRelation as DomainApprovedRelation,
)
from app.graph.domain import (
    ConceptCandidate as DomainConceptCandidate,
)
from app.graph.domain import (
    RelationCandidate as DomainRelationCandidate,
)
from app.graph.domain import (
    RelationType as DomainRelationType,
)
from app.graph.errors import GraphCoreError
from app.graph.outbox import GraphOutboxEvent
from app.graph.review import GraphReviewService, assert_prerequisite_publishable
from app.models import (
    Chunk,
    ConceptCandidate,
    Course,
    CourseIndex,
    CourseIndexStatus,
    CourseStatus,
    Document,
    DocumentVersion,
    DocumentVersionStatus,
    Enrollment,
    EnrollmentStatus,
    GraphOutbox,
    IndexComponentStatus,
    IngestionJob,
    IngestionStage,
    RelationCandidate,
    RelationType,
    ReviewStatus,
    User,
    UserRole,
)


class GraphService:
    """PostgreSQL-backed graph review and publication operations."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.review = GraphReviewService()

    async def list_candidates(
        self,
        course_id: uuid.UUID,
        teacher: User,
        *,
        status: ReviewStatus | None = None,
    ) -> dict[str, Any]:
        await self._owner_course(course_id, teacher)

        concept_statement = select(ConceptCandidate).where(
            ConceptCandidate.course_id == course_id,
            ConceptCandidate.deleted_at.is_(None),
        )
        relation_statement = select(RelationCandidate).where(
            RelationCandidate.course_id == course_id,
            RelationCandidate.deleted_at.is_(None),
        )
        if status is not None:
            concept_statement = concept_statement.where(
                ConceptCandidate.status == status
            )
            relation_statement = relation_statement.where(
                RelationCandidate.status == status
            )

        concepts = (
            await self.session.scalars(
                concept_statement.order_by(
                    ConceptCandidate.created_at.asc(), ConceptCandidate.id.asc()
                )
            )
        ).all()
        relations = (
            await self.session.scalars(
                relation_statement.order_by(
                    RelationCandidate.created_at.asc(), RelationCandidate.id.asc()
                )
            )
        ).all()
        sources = await self._sources(
            course_id,
            {candidate.source_chunk_id for candidate in (*concepts, *relations)},
        )
        concept_lookup = {candidate.id: candidate for candidate in concepts}
        missing_endpoint_ids = {
            concept_id
            for relation in relations
            for concept_id in (
                relation.from_candidate_id,
                relation.to_candidate_id,
            )
            if concept_id not in concept_lookup
        }
        if missing_endpoint_ids:
            endpoint_rows = (
                await self.session.scalars(
                    select(ConceptCandidate).where(
                        ConceptCandidate.id.in_(missing_endpoint_ids),
                        ConceptCandidate.course_id == course_id,
                        ConceptCandidate.deleted_at.is_(None),
                    )
                )
            ).all()
            concept_lookup.update(
                {candidate.id: candidate for candidate in endpoint_rows}
            )

        return {
            "course_id": course_id,
            "concepts": [
                self._concept_data(candidate, sources.get(candidate.source_chunk_id))
                for candidate in concepts
            ],
            "relations": [
                self._relation_data(
                    candidate,
                    sources.get(candidate.source_chunk_id),
                    concept_lookup,
                )
                for candidate in relations
            ],
        }

    async def edit_concept(
        self,
        candidate_id: uuid.UUID,
        teacher: User,
        changes: dict[str, Any],
    ) -> dict[str, Any]:
        candidate = await self._concept(candidate_id, for_update=True)
        await self._owner_course(candidate.course_id, teacher, for_update=True)
        self._require_pending(candidate.status)

        for field in ("name", "description", "aliases"):
            if field in changes:
                setattr(candidate, field, changes[field])
        await self.session.commit()
        source = await self._source(candidate.course_id, candidate.source_chunk_id)
        return self._concept_data(candidate, source)

    async def approve_concept(
        self,
        candidate_id: uuid.UUID,
        teacher: User,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        candidate = await self._concept(candidate_id, for_update=True)
        await self._owner_course(candidate.course_id, teacher, for_update=True)
        if candidate.status == ReviewStatus.APPROVED:
            source = await self._source(
                candidate.course_id, candidate.source_chunk_id, required=True
            )
            return self._concept_data(candidate, source)
        self._require_pending(candidate.status)
        source = await self._source(
            candidate.course_id, candidate.source_chunk_id, required=True
        )
        await self._validate_candidate_index(candidate)

        try:
            approval = self.review.approve_concept(
                DomainConceptCandidate(
                    id=candidate.id,
                    course_id=candidate.course_id,
                    name=candidate.name,
                    description=candidate.description,
                    source_chunk_id=candidate.source_chunk_id,
                    confidence=candidate.confidence,
                ),
                reviewer_id=teacher.id,
                name=name,
                description=description,
            )
        except GraphCoreError as exc:
            raise self._graph_error(exc) from exc

        candidate.name = approval.fact.name
        candidate.description = approval.fact.description
        candidate.status = ReviewStatus.APPROVED
        candidate.reviewed_by = teacher.id
        candidate.reviewed_at = approval.fact.reviewed_at
        self.session.add(self._outbox_record(approval.outbox_event))
        await self.session.commit()
        return self._concept_data(candidate, source)

    async def reject_concept(
        self, candidate_id: uuid.UUID, teacher: User
    ) -> dict[str, Any]:
        candidate = await self._concept(candidate_id, for_update=True)
        await self._owner_course(candidate.course_id, teacher, for_update=True)
        if candidate.status == ReviewStatus.REJECTED:
            source = await self._source(candidate.course_id, candidate.source_chunk_id)
            return self._concept_data(candidate, source)
        self._require_pending(candidate.status)
        candidate.status = ReviewStatus.REJECTED
        candidate.reviewed_by = teacher.id
        candidate.reviewed_at = datetime.now(UTC)
        await self.session.commit()
        source = await self._source(candidate.course_id, candidate.source_chunk_id)
        return self._concept_data(candidate, source)

    async def approve_relation(
        self,
        candidate_id: uuid.UUID,
        teacher: User,
        *,
        relation_type: RelationType | None = None,
    ) -> dict[str, Any]:
        candidate = await self._relation(candidate_id, for_update=True)
        await self._owner_course(candidate.course_id, teacher, for_update=True)
        if candidate.status == ReviewStatus.APPROVED:
            source = await self._source(
                candidate.course_id, candidate.source_chunk_id, required=True
            )
            endpoints = await self._relation_endpoints(candidate, require_approved=True)
            return self._relation_data(candidate, source, endpoints)
        self._require_pending(candidate.status)
        source = await self._source(
            candidate.course_id, candidate.source_chunk_id, required=True
        )
        endpoints = await self._relation_endpoints(candidate, require_approved=True)

        approved_rows = (
            await self.session.scalars(
                select(RelationCandidate)
                .where(
                    RelationCandidate.course_id == candidate.course_id,
                    RelationCandidate.status == ReviewStatus.APPROVED,
                    RelationCandidate.deleted_at.is_(None),
                )
                .with_for_update()
            )
        ).all()
        now = datetime.now(UTC)
        approved_relations = tuple(
            self._domain_approved_relation(row, reviewer=teacher, fallback_time=now)
            for row in approved_rows
        )
        try:
            approval = self.review.approve_relation(
                DomainRelationCandidate(
                    id=candidate.id,
                    course_id=candidate.course_id,
                    from_concept_id=candidate.from_candidate_id,
                    to_concept_id=candidate.to_candidate_id,
                    relation_type=DomainRelationType(candidate.type.value),
                    source_chunk_id=candidate.source_chunk_id,
                    confidence=candidate.confidence,
                ),
                approved_relations=approved_relations,
                reviewer_id=teacher.id,
                relation_type=(
                    DomainRelationType(relation_type.value)
                    if relation_type is not None
                    else None
                ),
            )
        except GraphCoreError as exc:
            raise self._graph_error(exc) from exc

        candidate.type = RelationType(approval.fact.relation_type.value)
        candidate.status = ReviewStatus.APPROVED
        candidate.reviewed_by = teacher.id
        candidate.reviewed_at = approval.fact.reviewed_at
        self.session.add(self._outbox_record(approval.outbox_event))
        await self.session.commit()
        return self._relation_data(candidate, source, endpoints)

    async def reject_relation(
        self, candidate_id: uuid.UUID, teacher: User
    ) -> dict[str, Any]:
        candidate = await self._relation(candidate_id, for_update=True)
        await self._owner_course(candidate.course_id, teacher, for_update=True)
        if candidate.status == ReviewStatus.REJECTED:
            source = await self._source(candidate.course_id, candidate.source_chunk_id)
            endpoints = await self._relation_endpoints(
                candidate, require_approved=False
            )
            return self._relation_data(candidate, source, endpoints)
        self._require_pending(candidate.status)
        candidate.status = ReviewStatus.REJECTED
        candidate.reviewed_by = teacher.id
        candidate.reviewed_at = datetime.now(UTC)
        await self.session.commit()
        source = await self._source(candidate.course_id, candidate.source_chunk_id)
        endpoints = await self._relation_endpoints(candidate, require_approved=False)
        return self._relation_data(candidate, source, endpoints)

    async def approved_graph(self, course_id: uuid.UUID, user: User) -> dict[str, Any]:
        await self._authorized_course(course_id, user)
        active_index = await self.session.scalar(
            select(CourseIndex).where(
                CourseIndex.course_id == course_id,
                CourseIndex.status == CourseIndexStatus.ACTIVE,
                CourseIndex.deleted_at.is_(None),
            )
        )
        if active_index is None:
            raise AppError(409, "ACTIVE_INDEX_REQUIRED", "The course has no active index")
        concepts = (
            await self.session.scalars(
                select(ConceptCandidate)
                .where(
                    ConceptCandidate.course_id == course_id,
                    ConceptCandidate.status == ReviewStatus.APPROVED,
                    ConceptCandidate.index_id == active_index.id,
                    ConceptCandidate.deleted_at.is_(None),
                )
                .order_by(ConceptCandidate.name.asc(), ConceptCandidate.id.asc())
            )
        ).all()
        concept_lookup = {candidate.id: candidate for candidate in concepts}
        concept_ids = set(concept_lookup)
        relations: list[RelationCandidate] = []
        if concept_ids:
            covered_version_ids = {
                uuid.UUID(str(value))
                for value in (active_index.covered_document_version_ids or ())
            }
            relations = list(
                (
                    await self.session.scalars(
                        select(RelationCandidate)
                        .join(Chunk, Chunk.id == RelationCandidate.source_chunk_id)
                        .where(
                            RelationCandidate.course_id == course_id,
                            RelationCandidate.status == ReviewStatus.APPROVED,
                            RelationCandidate.deleted_at.is_(None),
                            RelationCandidate.from_candidate_id.in_(concept_ids),
                            RelationCandidate.to_candidate_id.in_(concept_ids),
                            Chunk.version_id.in_(covered_version_ids),
                        )
                        .order_by(
                            RelationCandidate.type.asc(), RelationCandidate.id.asc()
                        )
                    )
                ).all()
            )
        return {
            "course_id": course_id,
            "published_index": {
                "id": active_index.id,
                "version": active_index.version,
                "published_at": active_index.published_at,
            },
            "concepts": [
                {
                    "id": candidate.id,
                    "name": candidate.name,
                    "description": candidate.description,
                    "aliases": candidate.aliases,
                    "source_chunk_id": candidate.source_chunk_id,
                    "status": candidate.status,
                }
                for candidate in concepts
            ],
            "relations": [
                {
                    "id": candidate.id,
                    "from_concept_id": candidate.from_candidate_id,
                    "to_concept_id": candidate.to_candidate_id,
                    "type": candidate.type,
                    "source_chunk_id": candidate.source_chunk_id,
                    "status": candidate.status,
                }
                for candidate in relations
            ],
        }

    async def publish(self, course_id: uuid.UUID, teacher: User) -> dict[str, Any]:
        await self._owner_course(course_id, teacher, for_update=True)
        target = await self.session.scalar(
            select(CourseIndex)
            .where(
                CourseIndex.course_id == course_id,
                CourseIndex.status == CourseIndexStatus.READY,
                CourseIndex.deleted_at.is_(None),
            )
            .order_by(CourseIndex.version.desc())
            .limit(1)
            .with_for_update()
        )
        if target is None:
            raise AppError(
                409,
                "GRAPH_INDEX_NOT_READY",
                "No course index is ready for graph review publication",
            )
        if (
            target.dense_status != IndexComponentStatus.READY
            or target.lexical_status != IndexComponentStatus.READY
        ):
            raise AppError(
                409,
                "GRAPH_INDEX_COMPONENTS_NOT_READY",
                "Dense and lexical indexes must both be ready before publication",
                details={
                    "dense_status": target.dense_status.value,
                    "lexical_status": target.lexical_status.value,
                },
            )
        if target.covered_document_version_ids is None:
            raise AppError(
                409,
                "GRAPH_INDEX_COVERAGE_UNKNOWN",
                "This index predates corpus manifests and must be rebuilt before publication",
                details={"index_id": str(target.id), "index_version": target.version},
            )

        pending_concepts = await self.session.scalar(
            select(func.count())
            .select_from(ConceptCandidate)
            .where(
                ConceptCandidate.course_id == course_id,
                ConceptCandidate.status == ReviewStatus.PENDING,
                ConceptCandidate.deleted_at.is_(None),
            )
        )
        pending_relations = await self.session.scalar(
            select(func.count())
            .select_from(RelationCandidate)
            .where(
                RelationCandidate.course_id == course_id,
                RelationCandidate.status == ReviewStatus.PENDING,
                RelationCandidate.deleted_at.is_(None),
            )
        )
        if pending_concepts or pending_relations:
            raise AppError(
                409,
                "GRAPH_REVIEW_PENDING",
                "All graph candidates must be reviewed before publication",
                details={
                    "pending_concepts": int(pending_concepts or 0),
                    "pending_relations": int(pending_relations or 0),
                },
            )
        await self._validate_approved_graph(course_id, teacher)

        previous_ids = list(
            (
                await self.session.scalars(
                    select(CourseIndex.id)
                    .where(
                        CourseIndex.course_id == course_id,
                        CourseIndex.status == CourseIndexStatus.ACTIVE,
                        CourseIndex.id != target.id,
                        CourseIndex.deleted_at.is_(None),
                    )
                    .with_for_update()
                )
            ).all()
        )
        if previous_ids:
            await self.session.execute(
                update(CourseIndex)
                .where(CourseIndex.id.in_(previous_ids))
                .values(status=CourseIndexStatus.ARCHIVED)
            )
            await self.session.flush()

        now = datetime.now(UTC)
        target.status = CourseIndexStatus.ACTIVE
        target.published_at = now

        # Only versions whose chunks are inside the target index's corpus may
        # become PUBLISHED here. Content ingested after the index was built is
        # left for the next build + publication cycle, keeping "new versions
        # serve only after full indexing" verifiable and citations version-true.
        covered_ids = target.covered_document_version_ids
        covered_version_ids = {uuid.UUID(value) for value in covered_ids if value}
        ready_filter = [
            Document.course_id == course_id,
            Document.deleted_at.is_(None),
            DocumentVersion.deleted_at.is_(None),
            DocumentVersion.status == DocumentVersionStatus.READY_FOR_REVIEW,
        ]
        ready_filter.append(DocumentVersion.id.in_(covered_version_ids))
        version_ids = list(
            (
                await self.session.scalars(
                    select(DocumentVersion.id)
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(*ready_filter)
                    .with_for_update()
                )
            ).all()
        )
        if version_ids:
            await self.session.execute(
                update(DocumentVersion)
                .where(DocumentVersion.id.in_(version_ids))
                .values(status=DocumentVersionStatus.PUBLISHED, published_at=now)
            )
            await self.session.execute(
                update(IngestionJob)
                .where(
                    IngestionJob.version_id.in_(version_ids),
                    IngestionJob.stage == IngestionStage.READY_FOR_REVIEW,
                )
                .values(
                    stage=IngestionStage.PUBLISHED,
                    progress=100,
                    finished_at=now,
                    error_code=None,
                    error_message=None,
                )
            )
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise AppError(
                409,
                "GRAPH_PUBLISH_CONFLICT",
                "The course graph publication conflicted with another publication",
            ) from exc
        return {
            "course_id": course_id,
            "index_id": target.id,
            "version": target.version,
            "status": target.status,
            "published_at": target.published_at,
            "superseded_index_ids": previous_ids,
            "published_document_version_ids": version_ids,
        }

    async def _owner_course(
        self,
        course_id: uuid.UUID,
        teacher: User,
        *,
        for_update: bool = False,
    ) -> Course:
        statement = select(Course).where(Course.id == course_id)
        if for_update:
            statement = statement.with_for_update()
        course = await self.session.scalar(statement)
        if course is None:
            raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
        if teacher.role != UserRole.TEACHER or course.owner_id != teacher.id:
            raise AppError(
                403,
                "COURSE_ACCESS_DENIED",
                "You do not have access to this course",
            )
        return course

    async def _authorized_course(self, course_id: uuid.UUID, user: User) -> Course:
        course = await self.session.scalar(select(Course).where(Course.id == course_id))
        if course is None:
            raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
        if user.role == UserRole.TEACHER:
            if course.owner_id != user.id:
                raise AppError(
                    403,
                    "COURSE_ACCESS_DENIED",
                    "You do not have access to this course",
                )
            return course
        if course.status != CourseStatus.ACTIVE:
            raise AppError(
                403,
                "COURSE_ACCESS_DENIED",
                "You do not have access to this course",
            )
        enrollment_id = await self.session.scalar(
            select(Enrollment.id).where(
                Enrollment.course_id == course_id,
                Enrollment.student_id == user.id,
                Enrollment.status == EnrollmentStatus.ACTIVE,
            )
        )
        if enrollment_id is None:
            raise AppError(
                403,
                "COURSE_ACCESS_DENIED",
                "You do not have access to this course",
            )
        return course

    async def _concept(
        self, candidate_id: uuid.UUID, *, for_update: bool = False
    ) -> ConceptCandidate:
        statement = select(ConceptCandidate).where(
            ConceptCandidate.id == candidate_id,
            ConceptCandidate.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        candidate = await self.session.scalar(statement)
        if candidate is None:
            raise AppError(
                404, "GRAPH_CONCEPT_NOT_FOUND", "Concept candidate not found"
            )
        return candidate

    async def _relation(
        self, candidate_id: uuid.UUID, *, for_update: bool = False
    ) -> RelationCandidate:
        statement = select(RelationCandidate).where(
            RelationCandidate.id == candidate_id,
            RelationCandidate.deleted_at.is_(None),
        )
        if for_update:
            statement = statement.with_for_update()
        candidate = await self.session.scalar(statement)
        if candidate is None:
            raise AppError(
                404, "GRAPH_RELATION_NOT_FOUND", "Relation candidate not found"
            )
        return candidate

    async def _source(
        self,
        course_id: uuid.UUID,
        source_chunk_id: uuid.UUID,
        *,
        required: bool = False,
    ) -> tuple[Chunk, DocumentVersion, Document] | None:
        row = (
            await self.session.execute(
                select(Chunk, DocumentVersion, Document)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Chunk.id == source_chunk_id,
                    Chunk.deleted_at.is_(None),
                    DocumentVersion.deleted_at.is_(None),
                    Document.deleted_at.is_(None),
                    Document.course_id == course_id,
                )
            )
        ).one_or_none()
        if row is None and required:
            raise AppError(
                409,
                "GRAPH_SOURCE_COURSE_MISMATCH",
                "The candidate source chunk must belong to the same course",
            )
        return row

    async def _sources(
        self, course_id: uuid.UUID, source_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[Chunk, DocumentVersion, Document]]:
        if not source_ids:
            return {}
        rows = (
            await self.session.execute(
                select(Chunk, DocumentVersion, Document)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Chunk.id.in_(source_ids),
                    Chunk.deleted_at.is_(None),
                    DocumentVersion.deleted_at.is_(None),
                    Document.deleted_at.is_(None),
                    Document.course_id == course_id,
                )
            )
        ).all()
        return {
            chunk.id: (chunk, version, document) for chunk, version, document in rows
        }

    async def _validate_candidate_index(self, candidate: ConceptCandidate) -> None:
        if candidate.index_id is None:
            return
        index_id = await self.session.scalar(
            select(CourseIndex.id).where(
                CourseIndex.id == candidate.index_id,
                CourseIndex.course_id == candidate.course_id,
                CourseIndex.deleted_at.is_(None),
            )
        )
        if index_id is None:
            raise AppError(
                409,
                "GRAPH_CANDIDATE_INDEX_MISMATCH",
                "The concept candidate index must belong to the same course",
            )

    async def _relation_endpoints(
        self,
        candidate: RelationCandidate,
        *,
        require_approved: bool,
    ) -> dict[uuid.UUID, ConceptCandidate]:
        rows = (
            await self.session.scalars(
                select(ConceptCandidate).where(
                    ConceptCandidate.id.in_(
                        (candidate.from_candidate_id, candidate.to_candidate_id)
                    ),
                    ConceptCandidate.course_id == candidate.course_id,
                    ConceptCandidate.deleted_at.is_(None),
                )
            )
        ).all()
        lookup = {row.id: row for row in rows}
        if (
            len(lookup) != 2
            and candidate.from_candidate_id != candidate.to_candidate_id
        ):
            raise AppError(
                409,
                "GRAPH_RELATION_ENDPOINT_INVALID",
                "Relation endpoints must both belong to the same course",
            )
        if require_approved and any(
            lookup.get(concept_id) is None
            or lookup[concept_id].status != ReviewStatus.APPROVED
            for concept_id in (
                candidate.from_candidate_id,
                candidate.to_candidate_id,
            )
        ):
            raise AppError(
                409,
                "GRAPH_RELATION_ENDPOINT_NOT_APPROVED",
                "Relation endpoints must both be approved concepts",
            )
        return lookup

    async def _validate_approved_graph(
        self, course_id: uuid.UUID, reviewer: User
    ) -> None:
        concepts = (
            await self.session.scalars(
                select(ConceptCandidate).where(
                    ConceptCandidate.course_id == course_id,
                    ConceptCandidate.status == ReviewStatus.APPROVED,
                    ConceptCandidate.deleted_at.is_(None),
                )
            )
        ).all()
        approved_ids = {concept.id for concept in concepts}
        rows = (
            await self.session.scalars(
                select(RelationCandidate).where(
                    RelationCandidate.course_id == course_id,
                    RelationCandidate.status == ReviewStatus.APPROVED,
                    RelationCandidate.deleted_at.is_(None),
                )
            )
        ).all()
        now = datetime.now(UTC)
        accepted: list[DomainApprovedRelation] = []
        try:
            for row in rows:
                if (
                    row.from_candidate_id not in approved_ids
                    or row.to_candidate_id not in approved_ids
                ):
                    raise AppError(
                        409,
                        "GRAPH_APPROVED_RELATION_ENDPOINT_INVALID",
                        "Approved relations must reference approved concepts",
                    )
                candidate = DomainRelationCandidate(
                    id=row.id,
                    course_id=row.course_id,
                    from_concept_id=row.from_candidate_id,
                    to_concept_id=row.to_candidate_id,
                    relation_type=DomainRelationType(row.type.value),
                    source_chunk_id=row.source_chunk_id,
                    confidence=row.confidence,
                )
                assert_prerequisite_publishable(candidate, accepted)
                accepted.append(
                    self._domain_approved_relation(
                        row, reviewer=reviewer, fallback_time=now
                    )
                )
        except GraphCoreError as exc:
            raise self._graph_error(exc) from exc

    @staticmethod
    def _domain_approved_relation(
        row: RelationCandidate,
        *,
        reviewer: User,
        fallback_time: datetime,
    ) -> DomainApprovedRelation:
        return DomainApprovedRelation(
            id=row.id,
            course_id=row.course_id,
            from_concept_id=row.from_candidate_id,
            to_concept_id=row.to_candidate_id,
            relation_type=DomainRelationType(row.type.value),
            source_chunk_id=row.source_chunk_id,
            confidence=row.confidence,
            reviewer_id=row.reviewed_by or reviewer.id,
            reviewed_at=row.reviewed_at or fallback_time,
        )

    @staticmethod
    def _require_pending(status: ReviewStatus) -> None:
        if status != ReviewStatus.PENDING:
            raise AppError(
                409,
                "GRAPH_CANDIDATE_ALREADY_REVIEWED",
                "Only pending graph candidates can be changed",
                details={"status": status.value},
            )

    @staticmethod
    def _outbox_record(event: GraphOutboxEvent) -> GraphOutbox:
        return GraphOutbox(
            id=event.id,
            course_id=uuid.UUID(str(event.payload["course_id"])),
            event_type=event.event_type.value,
            aggregate_id=event.aggregate_id,
            deduplication_key=f"{event.event_type.value}:{event.aggregate_id}",
            payload=event.payload,
            available_at=event.occurred_at,
        )

    @staticmethod
    def _graph_error(exc: GraphCoreError) -> AppError:
        return AppError(409, exc.code, exc.message)

    @staticmethod
    def _source_data(
        source: tuple[Chunk, DocumentVersion, Document] | None,
    ) -> dict[str, Any] | None:
        if source is None:
            return None
        chunk, version, document = source
        return {
            "chunk_id": chunk.id,
            "content": chunk.content,
            "page": chunk.page,
            "section_path": chunk.section_path,
            "chunk_type": chunk.chunk_type,
            "document": {
                "id": document.id,
                "logical_name": document.logical_name,
                "version_id": version.id,
                "version": version.version,
            },
        }

    @classmethod
    def _concept_data(
        cls,
        candidate: ConceptCandidate,
        source: tuple[Chunk, DocumentVersion, Document] | None,
    ) -> dict[str, Any]:
        source_data = cls._source_data(source)
        return {
            "id": candidate.id,
            "course_id": candidate.course_id,
            "index_id": candidate.index_id,
            "name": candidate.name,
            "description": candidate.description,
            "aliases": candidate.aliases,
            "source_chunk_id": candidate.source_chunk_id,
            "evidence": candidate.evidence,
            "source_excerpt": (
                source_data["content"] if source_data is not None else None
            ),
            "extraction_model": candidate.extraction_model,
            "confidence": candidate.confidence,
            "status": candidate.status,
            "reviewed_by": candidate.reviewed_by,
            "reviewed_at": candidate.reviewed_at,
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
            "source": source_data,
        }

    @classmethod
    def _relation_data(
        cls,
        candidate: RelationCandidate,
        source: tuple[Chunk, DocumentVersion, Document] | None,
        concepts: dict[uuid.UUID, ConceptCandidate],
    ) -> dict[str, Any]:
        source_concept = concepts.get(candidate.from_candidate_id)
        target_concept = concepts.get(candidate.to_candidate_id)
        from_name = source_concept.name if source_concept is not None else None
        to_name = target_concept.name if target_concept is not None else None
        source_data = cls._source_data(source)
        return {
            "id": candidate.id,
            "course_id": candidate.course_id,
            "from_concept_id": candidate.from_candidate_id,
            "to_concept_id": candidate.to_candidate_id,
            "from_name": from_name,
            "to_name": to_name,
            "from_concept_name": from_name,
            "to_concept_name": to_name,
            "type": candidate.type,
            "relation_type": candidate.type,
            "source_chunk_id": candidate.source_chunk_id,
            "evidence": candidate.evidence,
            "source_excerpt": (
                source_data["content"] if source_data is not None else None
            ),
            "extraction_model": candidate.extraction_model,
            "confidence": candidate.confidence,
            "status": candidate.status,
            "reviewed_by": candidate.reviewed_by,
            "reviewed_at": candidate.reviewed_at,
            "created_at": candidate.created_at,
            "updated_at": candidate.updated_at,
            "source": source_data,
        }
