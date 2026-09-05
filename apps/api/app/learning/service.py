from __future__ import annotations

import uuid
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import case, exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.learning import (
    Concept as CoreConcept,
)
from app.learning import (
    LearningCoreError,
    PrerequisiteRelation,
    difficulty_weight,
    plan_learning_path,
    update_mastery,
)
from app.learning import (
    MasteryState as CoreMasteryState,
)
from app.models import (
    Chunk,
    ConceptCandidate,
    Course,
    CourseIndex,
    CourseIndexStatus,
    CourseStatus,
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    Enrollment,
    EnrollmentStatus,
    QuizAttempt,
    QuizAttemptStatus,
    QuizDifficulty,
    QuizItem,
    RecordStatus,
    RelationCandidate,
    RelationType,
    ReviewStatus,
    User,
)
from app.models import (
    MasteryState as DBMasteryState,
)

EXPLICIT_GENERATION_MODEL = "SOURCE_GROUNDED_EXPLICIT"
DETERMINISTIC_GENERATION_MODEL = "SOURCE_GROUNDED_DETERMINISTIC_V1"
MAX_GENERATED_CANDIDATES = 20


@dataclass(frozen=True, slots=True)
class QuizCandidateDraft:
    concept_id: uuid.UUID
    source_chunk_id: uuid.UUID
    question: str
    options: tuple[str, ...]
    answer: str
    explanation: str
    difficulty: QuizDifficulty


@dataclass(frozen=True, slots=True)
class AttemptOutcome:
    attempt: QuizAttempt
    mastery: DBMasteryState
    quiz: QuizItem
    replayed: bool


@dataclass(frozen=True, slots=True)
class LearningPathRecord:
    concept: ConceptCandidate
    mastery: float
    is_weak: bool
    depth: int
    reason: str
    quiz_item_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class LearningPathResult:
    target_concept_id: uuid.UUID
    max_depth: int
    steps: tuple[LearningPathRecord, ...]


class LearningService:
    """Transactional quiz and deterministic learning-path application service."""

    async def active_index(
        self, session: AsyncSession, course_id: uuid.UUID
    ) -> CourseIndex:
        """Resolve the course's ACTIVE index; shared with the agent service."""

        index = await session.scalar(
            select(CourseIndex).where(
                CourseIndex.course_id == course_id,
                CourseIndex.status == CourseIndexStatus.ACTIVE,
                CourseIndex.deleted_at.is_(None),
            )
        )
        if index is None:
            raise AppError(
                409, "ACTIVE_INDEX_REQUIRED", "The course has no active index"
            )
        return index

    async def require_owner_course(
        self,
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
        if course.owner_id != teacher.id:
            raise AppError(
                403,
                "COURSE_ACCESS_DENIED",
                "You do not have access to this course",
            )
        return course

    async def require_active_enrollment(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        student: User,
    ) -> Course:
        course = await session.scalar(select(Course).where(Course.id == course_id))
        if course is None:
            raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
        if course.status != CourseStatus.ACTIVE:
            raise AppError(
                403,
                "COURSE_ACCESS_DENIED",
                "You do not have access to this course",
            )
        enrollment_id = await session.scalar(
            select(Enrollment.id).where(
                Enrollment.course_id == course_id,
                Enrollment.student_id == student.id,
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

    async def generate_candidates(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        teacher: User,
        drafts: Sequence[QuizCandidateDraft],
    ) -> tuple[QuizItem, ...]:
        await self.require_owner_course(session, course_id, teacher)
        generation_model = EXPLICIT_GENERATION_MODEL
        if not drafts:
            drafts = await self._drafts_from_approved_evidence(session, course_id)
            generation_model = DETERMINISTIC_GENERATION_MODEL

        concept_ids = {draft.concept_id for draft in drafts}
        chunk_ids = {draft.source_chunk_id for draft in drafts}
        concepts = {
            concept.id: concept
            for concept in (
                await session.scalars(
                    select(ConceptCandidate).where(
                        ConceptCandidate.id.in_(concept_ids),
                        ConceptCandidate.course_id == course_id,
                    )
                )
            ).all()
        }
        chunks = {
            chunk.id: chunk
            for chunk in (
                await session.scalars(
                    select(Chunk)
                    .join(
                        DocumentVersion,
                        DocumentVersion.id == Chunk.version_id,
                    )
                    .join(Document, Document.id == DocumentVersion.document_id)
                    .where(
                        Chunk.id.in_(chunk_ids),
                        Chunk.status == RecordStatus.ACTIVE,
                        DocumentVersion.status == DocumentVersionStatus.PUBLISHED,
                        Document.status == DocumentStatus.ACTIVE,
                        Document.course_id == course_id,
                    )
                )
            ).all()
        }

        for draft in drafts:
            concept = concepts.get(draft.concept_id)
            if concept is None:
                raise AppError(
                    404,
                    "QUIZ_CONCEPT_NOT_FOUND",
                    "The quiz concept was not found in this course",
                    details={"concept_id": str(draft.concept_id)},
                )
            if concept.status != ReviewStatus.APPROVED:
                raise AppError(
                    409,
                    "QUIZ_CONCEPT_NOT_APPROVED",
                    "Quiz candidates require a teacher-approved concept",
                    details={
                        "concept_id": str(concept.id),
                        "status": concept.status.value,
                    },
                )
            if draft.source_chunk_id not in chunks:
                raise AppError(
                    409,
                    "QUIZ_SOURCE_NOT_PUBLISHED",
                    "The source chunk must belong to published course material",
                    details={"source_chunk_id": str(draft.source_chunk_id)},
                )

        items = tuple(
            QuizItem(
                course_id=course_id,
                concept_id=draft.concept_id,
                source_chunk_id=draft.source_chunk_id,
                question=draft.question,
                options=list(draft.options),
                answer=draft.answer,
                explanation=draft.explanation,
                difficulty=draft.difficulty,
                status=ReviewStatus.PENDING,
                generation_model=generation_model,
            )
            for draft in drafts
        )
        session.add_all(items)
        await session.commit()
        for item in items:
            await session.refresh(item)
        return items

    async def _drafts_from_approved_evidence(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
    ) -> tuple[QuizCandidateDraft, ...]:
        rows = (
            await session.execute(
                select(ConceptCandidate, Chunk)
                .join(Chunk, Chunk.id == ConceptCandidate.source_chunk_id)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    ConceptCandidate.course_id == course_id,
                    ConceptCandidate.status == ReviewStatus.APPROVED,
                    ConceptCandidate.deleted_at.is_(None),
                    Chunk.status == RecordStatus.ACTIVE,
                    DocumentVersion.status == DocumentVersionStatus.PUBLISHED,
                    Document.status == DocumentStatus.ACTIVE,
                    Document.course_id == course_id,
                )
                .order_by(
                    ConceptCandidate.created_at.asc(),
                    ConceptCandidate.id.asc(),
                )
                .limit(MAX_GENERATED_CANDIDATES)
            )
        ).all()

        drafts = tuple(
            self._deterministic_draft(concept, chunk)
            for concept, chunk in rows
            if chunk.content.strip()
        )
        if not drafts:
            raise AppError(
                409,
                "QUIZ_GENERATION_EVIDENCE_INSUFFICIENT",
                "No teacher-approved concept with published course evidence is available",
            )
        return drafts

    @staticmethod
    def _deterministic_draft(
        concept: ConceptCandidate,
        chunk: Chunk,
    ) -> QuizCandidateDraft:
        evidence = " ".join(chunk.content.split())[:500]
        answer = evidence
        options = [
            answer,
            f"课程资料未提供“{concept.name}”的来源证据。",
            f"“{concept.name}”仅来自未经教师审核的内容。",
        ]
        correct_position = int(concept.id.hex[:2], 16) % len(options)
        options[0], options[correct_position] = options[correct_position], options[0]
        return QuizCandidateDraft(
            concept_id=concept.id,
            source_chunk_id=chunk.id,
            question=f"下列哪一项是课程资料中与“{concept.name}”直接关联的原文证据？",
            options=tuple(options),
            answer=answer,
            explanation=f"答案直接引用该知识点关联的已发布课程资料：{evidence}",
            difficulty=QuizDifficulty.EASY,
        )

    async def list_review_items(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        teacher: User,
        *,
        status: ReviewStatus | None = ReviewStatus.PENDING,
    ) -> tuple[QuizItem, ...]:
        await self.require_owner_course(session, course_id, teacher)
        statement = select(QuizItem).where(QuizItem.course_id == course_id)
        if status is not None:
            statement = statement.where(QuizItem.status == status)
        return tuple(
            (
                await session.scalars(
                    statement.order_by(QuizItem.created_at.asc(), QuizItem.id.asc())
                )
            ).all()
        )

    async def review_item(
        self,
        session: AsyncSession,
        item_id: uuid.UUID,
        teacher: User,
        decision: ReviewStatus,
    ) -> QuizItem:
        if decision not in (ReviewStatus.APPROVED, ReviewStatus.REJECTED):
            raise ValueError("decision must be APPROVED or REJECTED")
        item = await session.scalar(
            select(QuizItem).where(QuizItem.id == item_id).with_for_update()
        )
        if item is None:
            raise AppError(404, "QUIZ_NOT_FOUND", "Quiz item not found")
        await self.require_owner_course(session, item.course_id, teacher)
        if item.status == decision:
            return item
        if item.status != ReviewStatus.PENDING:
            raise AppError(
                409,
                "QUIZ_REVIEW_CONFLICT",
                "The quiz item already has a different review decision",
                details={"status": item.status.value},
            )
        if decision == ReviewStatus.APPROVED:
            await self._require_approved_quiz_dependencies(session, item)
        item.status = decision
        item.reviewed_by = teacher.id
        item.reviewed_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(item)
        return item

    async def next_quiz(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        student: User,
        *,
        concept_id: uuid.UUID | None = None,
        difficulty: QuizDifficulty | None = None,
    ) -> QuizItem:
        await self.require_active_enrollment(session, course_id, student)
        active_index = await self.active_index(session, course_id)
        covered_versions = {
            uuid.UUID(str(value))
            for value in (active_index.covered_document_version_ids or ())
        }
        previously_attempted = exists(
            select(QuizAttempt.id).where(
                QuizAttempt.student_id == student.id,
                QuizAttempt.quiz_item_id == QuizItem.id,
                QuizAttempt.status == QuizAttemptStatus.GRADED,
            )
        )
        statement = (
            select(QuizItem)
            .join(
                ConceptCandidate,
                ConceptCandidate.id == QuizItem.concept_id,
            )
            .join(Chunk, Chunk.id == QuizItem.source_chunk_id)
            .where(
                QuizItem.course_id == course_id,
                QuizItem.status == ReviewStatus.APPROVED,
                ConceptCandidate.course_id == course_id,
                ConceptCandidate.status == ReviewStatus.APPROVED,
                ConceptCandidate.index_id == active_index.id,
                Chunk.version_id.in_(covered_versions),
                Chunk.status == RecordStatus.ACTIVE,
            )
        )
        if concept_id is not None:
            statement = statement.where(QuizItem.concept_id == concept_id)
        if difficulty is not None:
            statement = statement.where(QuizItem.difficulty == difficulty)
        item = await session.scalar(
            statement.order_by(
                case((previously_attempted, 1), else_=0),
                QuizItem.created_at.asc(),
                QuizItem.id.asc(),
            ).limit(1)
        )
        if item is None:
            raise AppError(
                404,
                "APPROVED_QUIZ_NOT_AVAILABLE",
                "No teacher-approved quiz item is available",
            )
        return item

    async def submit_attempt(
        self,
        session: AsyncSession,
        item_id: uuid.UUID,
        student: User,
        *,
        answer: str,
        idempotency_key: str,
    ) -> AttemptOutcome:
        student_id = student.id
        quiz = await session.scalar(
            select(QuizItem).where(QuizItem.id == item_id).with_for_update()
        )
        if quiz is None:
            raise AppError(404, "QUIZ_NOT_FOUND", "Quiz item not found")
        await self.require_active_enrollment(session, quiz.course_id, student)
        active_index = await self.active_index(session, quiz.course_id)
        current_concept = await session.scalar(
            select(ConceptCandidate.id).where(
                ConceptCandidate.id == quiz.concept_id,
                ConceptCandidate.course_id == quiz.course_id,
                ConceptCandidate.index_id == active_index.id,
                ConceptCandidate.status == ReviewStatus.APPROVED,
            )
        )
        covered_versions = {
            uuid.UUID(str(value))
            for value in (active_index.covered_document_version_ids or ())
        }
        current_source = await session.scalar(
            select(Chunk.id).where(
                Chunk.id == quiz.source_chunk_id,
                Chunk.version_id.in_(covered_versions),
                Chunk.status == RecordStatus.ACTIVE,
            )
        )
        if current_concept is None or current_source is None:
            raise AppError(
                409,
                "QUIZ_VERSION_STALE",
                "The quiz does not belong to the active course version",
            )

        # Different quiz rows can still update the same concept. Lock the
        # concept as the shared serialization point before reading mastery.
        await session.scalar(
            select(ConceptCandidate.id)
            .where(
                ConceptCandidate.id == quiz.concept_id,
                ConceptCandidate.course_id == quiz.course_id,
            )
            .with_for_update()
        )

        existing = await session.scalar(
            select(QuizAttempt).where(
                QuizAttempt.student_id == student_id,
                QuizAttempt.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            if existing.quiz_item_id != quiz.id:
                raise AppError(
                    409,
                    "IDEMPOTENCY_KEY_REUSED",
                    "The idempotency key belongs to another quiz attempt",
                )
            replayed_mastery = await self._mastery_for_attempt(
                session, quiz, student_id
            )
            return AttemptOutcome(existing, replayed_mastery, quiz, replayed=True)

        if quiz.status != ReviewStatus.APPROVED:
            raise AppError(
                409,
                "QUIZ_NOT_APPROVED",
                "Only teacher-approved quiz items can be attempted",
                details={"quiz_status": quiz.status.value},
            )
        await self._require_approved_quiz_dependencies(session, quiz)

        mastery: DBMasteryState | None = await session.scalar(
            select(DBMasteryState)
            .where(
                DBMasteryState.course_id == quiz.course_id,
                DBMasteryState.student_id == student_id,
                DBMasteryState.concept_id == quiz.concept_id,
                DBMasteryState.status == RecordStatus.ACTIVE,
            )
            .with_for_update()
        )
        if mastery is None:
            mastery = DBMasteryState(
                course_id=quiz.course_id,
                student_id=student_id,
                concept_id=quiz.concept_id,
                alpha=1.0,
                beta=1.0,
                attempt_count=0,
                status=RecordStatus.ACTIVE,
            )
            session.add(mastery)

        normalized_answer = answer.strip()
        correct = normalized_answer == quiz.answer
        before = CoreMasteryState(
            alpha=mastery.alpha,
            beta=mastery.beta,
            attempt_count=mastery.attempt_count,
        )
        after = update_mastery(
            before,
            correct=correct,
            difficulty=quiz.difficulty,
            quiz_status=quiz.status,
        )
        now = datetime.now(UTC)
        attempt = QuizAttempt(
            id=uuid.uuid4(),
            quiz_item_id=quiz.id,
            student_id=student_id,
            answer=normalized_answer,
            correct=correct,
            idempotency_key=idempotency_key,
            weight=difficulty_weight(quiz.difficulty),
            status=QuizAttemptStatus.GRADED,
            submitted_at=now,
            graded_at=now,
        )
        session.add(attempt)
        quiz_id = quiz.id
        try:
            # Flush the attempt first because MasteryState.last_attempt_id has a
            # foreign key but the models intentionally have no ORM relationship.
            await session.flush([attempt])
            mastery.alpha = after.alpha
            mastery.beta = after.beta
            mastery.attempt_count = after.attempt_count
            mastery.last_assessed_at = now
            mastery.last_attempt_id = attempt.id
            # QuizAttempt and MasteryState are committed by this single transaction.
            await session.commit()
        except IntegrityError:
            # A concurrent request with the same student/key may have won. This
            # transaction (including its mastery mutation) has been rolled back.
            await session.rollback()
            existing = await session.scalar(
                select(QuizAttempt).where(
                    QuizAttempt.student_id == student_id,
                    QuizAttempt.idempotency_key == idempotency_key,
                )
            )
            if existing is None:
                raise
            if existing.quiz_item_id != quiz_id:
                raise AppError(
                    409,
                    "IDEMPOTENCY_KEY_REUSED",
                    "The idempotency key belongs to another quiz attempt",
                )
            replay_quiz = await session.scalar(
                select(QuizItem).where(QuizItem.id == quiz_id)
            )
            if replay_quiz is None:
                raise AppError(404, "QUIZ_NOT_FOUND", "Quiz item not found")
            mastery = await self._mastery_for_attempt(session, replay_quiz, student_id)
            return AttemptOutcome(existing, mastery, replay_quiz, replayed=True)

        await session.refresh(attempt)
        await session.refresh(mastery)
        return AttemptOutcome(attempt, mastery, quiz, replayed=False)

    async def list_mastery(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        student: User,
    ) -> tuple[tuple[DBMasteryState, ConceptCandidate], ...]:
        await self.require_active_enrollment(session, course_id, student)
        active_index = await self.active_index(session, course_id)
        rows = (
            await session.execute(
                select(DBMasteryState, ConceptCandidate)
                .join(
                    ConceptCandidate,
                    ConceptCandidate.id == DBMasteryState.concept_id,
                )
                .where(
                    DBMasteryState.course_id == course_id,
                    DBMasteryState.student_id == student.id,
                    DBMasteryState.status == RecordStatus.ACTIVE,
                    ConceptCandidate.course_id == course_id,
                    ConceptCandidate.status == ReviewStatus.APPROVED,
                    ConceptCandidate.index_id == active_index.id,
                )
                .order_by(ConceptCandidate.name.asc(), ConceptCandidate.id.asc())
            )
        ).all()
        return tuple((mastery_row, concept_row) for mastery_row, concept_row in rows)

    async def learning_path(
        self,
        session: AsyncSession,
        course_id: uuid.UUID,
        student: User,
        *,
        target_concept_id: uuid.UUID,
        max_depth: int,
    ) -> LearningPathResult:
        await self.require_active_enrollment(session, course_id, student)
        active_index = await self.active_index(session, course_id)
        target = await session.scalar(
            select(ConceptCandidate).where(
                ConceptCandidate.id == target_concept_id,
                ConceptCandidate.course_id == course_id,
                ConceptCandidate.index_id == active_index.id,
            )
        )
        if target is None:
            raise AppError(
                404,
                "LEARNING_PATH_TARGET_NOT_FOUND",
                "The target concept was not found in this course",
                details={"target_concept_id": str(target_concept_id)},
            )

        concepts = tuple(
            (
                await session.scalars(
                    select(ConceptCandidate)
                    .where(
                        ConceptCandidate.course_id == course_id,
                        ConceptCandidate.index_id == active_index.id,
                    )
                    .order_by(
                        ConceptCandidate.created_at.asc(), ConceptCandidate.id.asc()
                    )
                )
            ).all()
        )
        relations = tuple(
            (
                await session.scalars(
                    select(RelationCandidate)
                    .join(Chunk, Chunk.id == RelationCandidate.source_chunk_id)
                    .where(
                        RelationCandidate.course_id == course_id,
                        RelationCandidate.type == RelationType.PREREQUISITE_OF,
                        RelationCandidate.status == ReviewStatus.APPROVED,
                        Chunk.version_id.in_(
                            uuid.UUID(str(value))
                            for value in (
                                active_index.covered_document_version_ids or ()
                            )
                        ),
                        Chunk.status == RecordStatus.ACTIVE,
                        RelationCandidate.from_candidate_id.in_(
                            select(ConceptCandidate.id).where(
                                ConceptCandidate.index_id == active_index.id
                            )
                        ),
                        RelationCandidate.to_candidate_id.in_(
                            select(ConceptCandidate.id).where(
                                ConceptCandidate.index_id == active_index.id
                            )
                        ),
                    )
                    .order_by(
                        RelationCandidate.created_at.asc(),
                        RelationCandidate.id.asc(),
                    )
                )
            ).all()
        )
        mastery_rows = tuple(
            (
                await session.scalars(
                    select(DBMasteryState).where(
                        DBMasteryState.course_id == course_id,
                        DBMasteryState.student_id == student.id,
                        DBMasteryState.status == RecordStatus.ACTIVE,
                    )
                )
            ).all()
        )

        core_concepts = tuple(
            CoreConcept(
                concept_id=concept.id,
                status=concept.status,
                name=concept.name,
            )
            for concept in concepts
        )
        core_relations = tuple(
            PrerequisiteRelation(
                prerequisite_id=relation.from_candidate_id,
                concept_id=relation.to_candidate_id,
                status=relation.status,
                relation_type=relation.type,
            )
            for relation in relations
        )
        mastery_by_concept: dict[Hashable, float | CoreMasteryState] = {
            state.concept_id: state.mastery for state in mastery_rows
        }
        try:
            planned = plan_learning_path(
                target_concept_id,
                core_concepts,
                core_relations,
                mastery_by_concept,
                max_depth=max_depth,
            )
        except LearningCoreError as exc:
            status_code = 404 if exc.code == "LEARNING_PATH_TARGET_NOT_FOUND" else 409
            raise AppError(
                status_code,
                exc.code,
                exc.message,
                details=exc.details,
            ) from exc

        concept_by_id = {concept.id: concept for concept in concepts}
        selected_ids = set(planned.concept_ids)
        approved_quizzes = tuple(
            (
                await session.scalars(
                    select(QuizItem)
                    .where(
                        QuizItem.course_id == course_id,
                        QuizItem.concept_id.in_(selected_ids),
                        QuizItem.status == ReviewStatus.APPROVED,
                    )
                    .order_by(QuizItem.created_at.asc(), QuizItem.id.asc())
                )
            ).all()
        )
        quiz_by_concept: dict[uuid.UUID, uuid.UUID] = {}
        for quiz in approved_quizzes:
            quiz_by_concept.setdefault(quiz.concept_id, quiz.id)

        path_steps: list[LearningPathRecord] = []
        for step in planned.steps:
            step_concept_id = step.concept_id
            if not isinstance(step_concept_id, uuid.UUID):
                raise AppError(
                    500,
                    "INTERNAL_ERROR",
                    "Learning path planner returned a non-UUID concept id",
                )
            path_steps.append(
                LearningPathRecord(
                    concept=concept_by_id[step_concept_id],
                    mastery=step.mastery,
                    is_weak=step.is_weak,
                    depth=step.depth,
                    reason=step.reason,
                    quiz_item_id=quiz_by_concept.get(step_concept_id),
                )
            )

        return LearningPathResult(
            target_concept_id=target_concept_id,
            max_depth=max_depth,
            steps=tuple(path_steps),
        )

    async def _require_approved_quiz_dependencies(
        self,
        session: AsyncSession,
        quiz: QuizItem,
    ) -> None:
        concept = await session.scalar(
            select(ConceptCandidate).where(
                ConceptCandidate.id == quiz.concept_id,
                ConceptCandidate.course_id == quiz.course_id,
            )
        )
        if concept is None or concept.status != ReviewStatus.APPROVED:
            raise AppError(
                409,
                "QUIZ_CONCEPT_NOT_APPROVED",
                "The quiz concept is not teacher-approved",
            )
        candidate_index = await session.scalar(
            select(CourseIndex).where(
                CourseIndex.id == concept.index_id,
                CourseIndex.course_id == quiz.course_id,
                CourseIndex.status.in_(
                    {CourseIndexStatus.READY, CourseIndexStatus.ACTIVE}
                ),
                CourseIndex.deleted_at.is_(None),
            )
        )
        if candidate_index is None:
            raise AppError(
                409,
                "QUIZ_INDEX_NOT_READY",
                "The quiz concept index is not ready for review",
            )
        source_id = await session.scalar(
            select(Chunk.id)
            .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                Chunk.id == quiz.source_chunk_id,
                Chunk.status == RecordStatus.ACTIVE,
                DocumentVersion.status == DocumentVersionStatus.PUBLISHED,
                DocumentVersion.id.in_(
                    uuid.UUID(str(value))
                    for value in (candidate_index.covered_document_version_ids or ())
                ),
                Document.status == DocumentStatus.ACTIVE,
                Document.course_id == quiz.course_id,
            )
        )
        if source_id is None:
            raise AppError(
                409,
                "QUIZ_SOURCE_NOT_PUBLISHED",
                "The quiz source is not published course material",
            )

    async def _mastery_for_attempt(
        self,
        session: AsyncSession,
        quiz: QuizItem,
        student_id: uuid.UUID,
    ) -> DBMasteryState:
        mastery = await session.scalar(
            select(DBMasteryState).where(
                DBMasteryState.course_id == quiz.course_id,
                DBMasteryState.student_id == student_id,
                DBMasteryState.concept_id == quiz.concept_id,
                DBMasteryState.status == RecordStatus.ACTIVE,
            )
        )
        if mastery is None:
            # A persisted graded attempt and no corresponding mastery row would
            # violate the same-transaction invariant and must not be hidden.
            raise AppError(
                500,
                "MASTERY_STATE_MISSING",
                "The stored quiz attempt has no mastery state",
            )
        return mastery


learning_service = LearningService()
