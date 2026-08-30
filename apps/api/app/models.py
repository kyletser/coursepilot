from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utc_now() -> datetime:
    return datetime.now(UTC)


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC and always return timezone-aware values, including on SQLite."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("UTCDateTime values must be timezone-aware")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, _dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UserRole(str, enum.Enum):
    STUDENT = "STUDENT"
    TEACHER = "TEACHER"


class UserStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"


class CourseTemplate(str, enum.Enum):
    DATA_STRUCTURES = "DATA_STRUCTURES"
    OPERATING_SYSTEMS = "OPERATING_SYSTEMS"


class CourseStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class EnrollmentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    LEFT = "LEFT"


class DocumentStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class DocumentVersionStatus(str, enum.Enum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class ChunkType(str, enum.Enum):
    TEXT = "TEXT"
    LIST = "LIST"
    TABLE = "TABLE"
    CODE_BLOCK = "CODE_BLOCK"
    FORMULA = "FORMULA"
    EXAMPLE = "EXAMPLE"
    ANSWER = "ANSWER"
    LAB_STEP = "LAB_STEP"


class RecordStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class IngestionStage(str, enum.Enum):
    QUEUED = "QUEUED"
    PARSING = "PARSING"
    CHUNKING = "CHUNKING"
    EMBEDDING = "EMBEDDING"
    LEXICAL_INDEXING = "LEXICAL_INDEXING"
    GRAPH_EXTRACTING = "GRAPH_EXTRACTING"
    VALIDATING = "VALIDATING"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class IndexComponentStatus(str, enum.Enum):
    PENDING = "PENDING"
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"


class CourseIndexStatus(str, enum.Enum):
    BUILDING = "BUILDING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class ReviewStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MERGED = "MERGED"


class RelationType(str, enum.Enum):
    PREREQUISITE_OF = "PREREQUISITE_OF"
    RELATED_TO = "RELATED_TO"
    PART_OF = "PART_OF"
    CONTRASTS_WITH = "CONTRASTS_WITH"


class GraphOutboxStatus(str, enum.Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class ChatSessionStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class MessageRole(str, enum.Enum):
    USER = "USER"
    ASSISTANT = "ASSISTANT"
    SYSTEM = "SYSTEM"


class AgentIntent(str, enum.Enum):
    TUTOR_QA = "TUTOR_QA"
    CONCEPT_COMPARE = "CONCEPT_COMPARE"
    DIAGNOSE = "DIAGNOSE"
    QUIZ = "QUIZ"
    LEARNING_PATH = "LEARNING_PATH"


class MessageStatus(str, enum.Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class QuizDifficulty(str, enum.Enum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class QuizAttemptStatus(str, enum.Enum):
    SUBMITTED = "SUBMITTED"
    GRADED = "GRADED"
    INVALIDATED = "INVALIDATED"


class EvalDatasetType(str, enum.Enum):
    RETRIEVAL = "RETRIEVAL"
    END_TO_END_QA = "END_TO_END_QA"
    INTENT_ROUTING = "INTENT_ROUTING"
    LEARNING_PATH = "LEARNING_PATH"


class EvalDatasetStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"
    ARCHIVED = "ARCHIVED"


class EvalRunStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class BadCaseSourceType(str, enum.Enum):
    MESSAGE = "MESSAGE"
    EVAL_RUN = "EVAL_RUN"
    QUIZ_ATTEMPT = "QUIZ_ATTEMPT"
    RETRIEVAL_TRACE = "RETRIEVAL_TRACE"


class BadCaseStatus(str, enum.Enum):
    OPEN = "OPEN"
    FIXED = "FIXED"
    WONT_FIX = "WONT_FIX"


user_role_enum = Enum(
    UserRole,
    name="user_role",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
user_status_enum = Enum(
    UserStatus,
    name="user_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
course_template_enum = Enum(
    CourseTemplate,
    name="course_template",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
course_status_enum = Enum(
    CourseStatus,
    name="course_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
enrollment_status_enum = Enum(
    EnrollmentStatus,
    name="enrollment_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
document_status_enum = Enum(
    DocumentStatus,
    name="document_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
document_version_status_enum = Enum(
    DocumentVersionStatus,
    name="document_version_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
chunk_type_enum = Enum(
    ChunkType,
    name="chunk_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
record_status_enum = Enum(
    RecordStatus,
    name="record_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
ingestion_stage_enum = Enum(
    IngestionStage,
    name="ingestion_stage",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
index_dense_status_enum = Enum(
    IndexComponentStatus,
    name="index_dense_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
index_lexical_status_enum = Enum(
    IndexComponentStatus,
    name="index_lexical_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
course_index_status_enum = Enum(
    CourseIndexStatus,
    name="course_index_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
review_status_enum = Enum(
    ReviewStatus,
    name="review_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
relation_type_enum = Enum(
    RelationType,
    name="relation_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
graph_outbox_status_enum = Enum(
    GraphOutboxStatus,
    name="graph_outbox_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
chat_session_status_enum = Enum(
    ChatSessionStatus,
    name="chat_session_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
message_role_enum = Enum(
    MessageRole,
    name="message_role",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
agent_intent_enum = Enum(
    AgentIntent,
    name="agent_intent",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
message_status_enum = Enum(
    MessageStatus,
    name="message_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
quiz_difficulty_enum = Enum(
    QuizDifficulty,
    name="quiz_difficulty",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
quiz_attempt_status_enum = Enum(
    QuizAttemptStatus,
    name="quiz_attempt_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
eval_dataset_type_enum = Enum(
    EvalDatasetType,
    name="eval_dataset_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
eval_dataset_status_enum = Enum(
    EvalDatasetStatus,
    name="eval_dataset_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
eval_run_status_enum = Enum(
    EvalRunStatus,
    name="eval_run_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
bad_case_source_type_enum = Enum(
    BadCaseSourceType,
    name="bad_case_source_type",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)
bad_case_status_enum = Enum(
    BadCaseStatus,
    name="bad_case_status",
    native_enum=False,
    create_constraint=True,
    validate_strings=True,
)


class UUIDPrimaryKeyMixin:
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class TimestampedMixin:
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(user_role_enum, nullable=False)
    status: Mapped[UserStatus] = mapped_column(
        user_status_enum, nullable=False, default=UserStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    owned_courses: Mapped[list[Course]] = relationship(back_populates="owner")
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="student")
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", foreign_keys="RefreshToken.user_id"
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_token_hash"),
        UniqueConstraint("parent_id", name="uq_refresh_tokens_parent_id"),
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_family_id", "family_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    family_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("refresh_tokens.id", ondelete="SET NULL"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )

    user: Mapped[User] = relationship(
        back_populates="refresh_tokens", foreign_keys=[user_id]
    )
    parent: Mapped[RefreshToken | None] = relationship(
        remote_side=[id], foreign_keys=[parent_id]
    )


class Course(Base):
    __tablename__ = "courses"
    __table_args__ = (Index("ix_courses_owner_id", "owner_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    template: Mapped[CourseTemplate] = mapped_column(
        course_template_enum, nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    semester: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[CourseStatus] = mapped_column(
        course_status_enum, nullable=False, default=CourseStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    owner: Mapped[User] = relationship(back_populates="owned_courses")
    invites: Mapped[list[CourseInvite]] = relationship(back_populates="course")
    enrollments: Mapped[list[Enrollment]] = relationship(back_populates="course")


class CourseInvite(Base):
    __tablename__ = "course_invites"
    __table_args__ = (
        UniqueConstraint("code_hash", name="uq_course_invites_code_hash"),
        Index("ix_course_invites_course_id", "course_id"),
        Index("ix_course_invites_expires_at", "expires_at"),
        Index(
            "uq_course_invites_one_active",
            "course_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
            sqlite_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )

    course: Mapped[Course] = relationship(back_populates="invites")


class Enrollment(Base):
    __tablename__ = "enrollments"
    __table_args__ = (
        UniqueConstraint(
            "course_id", "student_id", name="uq_enrollments_course_student"
        ),
        Index("ix_enrollments_student_status", "student_id", "status"),
        Index("ix_enrollments_course_status", "course_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[EnrollmentStatus] = mapped_column(
        enrollment_status_enum, nullable=False, default=EnrollmentStatus.ACTIVE
    )
    joined_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now, onupdate=utc_now
    )

    course: Mapped[Course] = relationship(back_populates="enrollments")
    student: Mapped[User] = relationship(back_populates="enrollments")


class Document(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        UniqueConstraint(
            "course_id", "logical_name", name="uq_documents_course_logical_name"
        ),
        Index("ix_documents_course_status", "course_id", "status"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    logical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        document_status_enum, nullable=False, default=DocumentStatus.ACTIVE
    )


class DocumentVersion(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "sha256", name="uq_document_versions_document_sha256"
        ),
        UniqueConstraint(
            "document_id", "version", name="uq_document_versions_document_version"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        CheckConstraint("size_bytes >= 0", name="size_bytes_nonnegative"),
        Index("ix_document_versions_document_status", "document_id", "status"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    media_type: Mapped[str] = mapped_column(String(127), nullable=False)
    file_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[DocumentVersionStatus] = mapped_column(
        document_version_status_enum,
        nullable=False,
        default=DocumentVersionStatus.UPLOADED,
    )
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Chunk(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("version_id", "ordinal", name="uq_chunks_version_ordinal"),
        CheckConstraint("ordinal >= 0", name="ordinal_nonnegative"),
        CheckConstraint("page IS NULL OR page > 0", name="page_positive"),
        CheckConstraint("char_count >= 0", name="char_count_nonnegative"),
        Index("ix_chunks_version_status", "version_id", "status"),
        Index("ix_chunks_version_page", "version_id", "page"),
        Index("ix_chunks_previous_chunk_id", "previous_chunk_id"),
        Index("ix_chunks_next_chunk_id", "next_chunk_id"),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    page: Mapped[int | None] = mapped_column(Integer)
    section_path: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    title_level: Mapped[int | None] = mapped_column(Integer)
    chunk_type: Mapped[ChunkType] = mapped_column(
        chunk_type_enum, nullable=False, default=ChunkType.TEXT
    )
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    previous_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL")
    )
    next_chunk_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("chunks.id", ondelete="SET NULL")
    )
    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(1024).with_variant(JSON(), "sqlite")
    )
    status: Mapped[RecordStatus] = mapped_column(
        record_status_enum, nullable=False, default=RecordStatus.ACTIVE
    )


class IngestionJob(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint("version_id", name="uq_ingestion_jobs_version_id"),
        CheckConstraint(
            "progress >= 0 AND progress <= 100", name="progress_percentage"
        ),
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        Index("ix_ingestion_jobs_stage_updated", "stage", "updated_at"),
    )

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[IngestionStage] = mapped_column(
        ingestion_stage_enum, nullable=False, default=IngestionStage.QUEUED
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage_details: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class CourseIndex(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "course_indexes"
    __table_args__ = (
        UniqueConstraint(
            "course_id", "version", name="uq_course_indexes_course_version"
        ),
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_course_indexes_course_status", "course_id", "status"),
        Index(
            "uq_course_indexes_one_active",
            "course_id",
            unique=True,
            postgresql_where=text("status = 'ACTIVE'"),
            sqlite_where=text("status = 'ACTIVE'"),
        ),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dense_status: Mapped[IndexComponentStatus] = mapped_column(
        index_dense_status_enum,
        nullable=False,
        default=IndexComponentStatus.PENDING,
    )
    lexical_status: Mapped[IndexComponentStatus] = mapped_column(
        index_lexical_status_enum,
        nullable=False,
        default=IndexComponentStatus.PENDING,
    )
    lexical_path: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[CourseIndexStatus] = mapped_column(
        course_index_status_enum,
        nullable=False,
        default=CourseIndexStatus.BUILDING,
    )
    smoke_test_result: Mapped[dict[str, object] | None] = mapped_column(JSON)
    validated_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ConceptCandidate(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "concept_candidates"
    __table_args__ = (
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="confidence_probability"
        ),
        Index("ix_concept_candidates_course_status", "course_id", "status"),
        Index("ix_concept_candidates_source_chunk_id", "source_chunk_id"),
        Index("ix_concept_candidates_index_id", "index_id"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    index_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("course_indexes.id", ondelete="SET NULL")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False
    )
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_model: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[ReviewStatus] = mapped_column(
        review_status_enum, nullable=False, default=ReviewStatus.PENDING
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    merged_into_candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("concept_candidates.id", ondelete="SET NULL")
    )


class RelationCandidate(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "relation_candidates"
    __table_args__ = (
        UniqueConstraint(
            "from_candidate_id",
            "to_candidate_id",
            "type",
            "source_chunk_id",
            name="uq_relation_candidates_edge_source",
        ),
        CheckConstraint(
            "from_candidate_id <> to_candidate_id", name="no_candidate_self_loop"
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="confidence_probability"
        ),
        Index("ix_relation_candidates_course_status", "course_id", "status"),
        Index("ix_relation_candidates_source_chunk_id", "source_chunk_id"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    from_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    to_candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    type: Mapped[RelationType] = mapped_column(relation_type_enum, nullable=False)
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False
    )
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_model: Mapped[str] = mapped_column(String(255), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[ReviewStatus] = mapped_column(
        review_status_enum, nullable=False, default=ReviewStatus.PENDING
    )
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class GraphOutbox(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "graph_outbox"
    __table_args__ = (
        UniqueConstraint("deduplication_key", name="uq_graph_outbox_deduplication_key"),
        CheckConstraint("retry_count >= 0", name="retry_count_nonnegative"),
        Index("ix_graph_outbox_status_available", "status", "available_at"),
        Index("ix_graph_outbox_course_id", "course_id"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    deduplication_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[GraphOutboxStatus] = mapped_column(
        graph_outbox_status_enum,
        nullable=False,
        default=GraphOutboxStatus.PENDING,
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_error: Mapped[str | None] = mapped_column(Text)


class ChatSession(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        CheckConstraint("index_version > 0", name="index_version_positive"),
        Index(
            "ix_chat_sessions_student_course_status",
            "student_id",
            "course_id",
            "status",
        ),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[ChatSessionStatus] = mapped_column(
        chat_session_status_enum,
        nullable=False,
        default=ChatSessionStatus.ACTIVE,
    )


class Message(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_session_created", "session_id", "created_at"),
        Index("ix_messages_trace_id", "trace_id"),
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[MessageRole] = mapped_column(message_role_enum, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    intent: Mapped[AgentIntent | None] = mapped_column(agent_intent_enum)
    trace_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    status: Mapped[MessageStatus] = mapped_column(
        message_status_enum, nullable=False, default=MessageStatus.PENDING
    )
    usage: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))


class Citation(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "citations"
    __table_args__ = (
        UniqueConstraint("message_id", "label", name="uq_citations_message_label"),
        UniqueConstraint(
            "message_id",
            "claim_index",
            "chunk_id",
            name="uq_citations_message_claim_chunk",
        ),
        CheckConstraint("label > 0", name="label_positive"),
        CheckConstraint("claim_index >= 0", name="claim_index_nonnegative"),
        Index("ix_citations_chunk_id", "chunk_id"),
    )

    message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False
    )
    quote: Mapped[str] = mapped_column(Text, nullable=False)
    claim_index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RecordStatus] = mapped_column(
        record_status_enum, nullable=False, default=RecordStatus.ACTIVE
    )


class QuizItem(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "quiz_items"
    __table_args__ = (
        Index("ix_quiz_items_course_status", "course_id", "status"),
        Index("ix_quiz_items_concept_status", "concept_id", "status"),
        Index("ix_quiz_items_source_chunk_id", "source_chunk_id"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    source_chunk_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    options: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    difficulty: Mapped[QuizDifficulty] = mapped_column(
        quiz_difficulty_enum, nullable=False
    )
    status: Mapped[ReviewStatus] = mapped_column(
        review_status_enum, nullable=False, default=ReviewStatus.PENDING
    )
    generation_model: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class QuizAttempt(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "quiz_attempts"
    __table_args__ = (
        UniqueConstraint(
            "student_id",
            "idempotency_key",
            name="uq_quiz_attempts_student_idempotency_key",
        ),
        CheckConstraint("weight > 0", name="weight_positive"),
        Index("ix_quiz_attempts_quiz_student", "quiz_item_id", "student_id"),
        Index("ix_quiz_attempts_student_created", "student_id", "created_at"),
    )

    quiz_item_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("quiz_items.id", ondelete="RESTRICT"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    weight: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[QuizAttemptStatus] = mapped_column(
        quiz_attempt_status_enum,
        nullable=False,
        default=QuizAttemptStatus.SUBMITTED,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=utc_now
    )
    graded_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class MasteryState(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "mastery_states"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "student_id",
            "concept_id",
            name="uq_mastery_states_course_student_concept",
        ),
        CheckConstraint("alpha > 0", name="alpha_positive"),
        CheckConstraint("beta > 0", name="beta_positive"),
        CheckConstraint("mastery >= 0 AND mastery <= 1", name="mastery_probability"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        Index("ix_mastery_states_student_course", "student_id", "course_id"),
        Index("ix_mastery_states_course_concept", "course_id", "concept_id"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    student_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    concept_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("concept_candidates.id", ondelete="RESTRICT"), nullable=False
    )
    alpha: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    beta: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    mastery: Mapped[float] = mapped_column(
        Float,
        Computed("alpha / (alpha + beta)", persisted=True),
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_assessed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_attempt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("quiz_attempts.id", ondelete="SET NULL")
    )
    status: Mapped[RecordStatus] = mapped_column(
        record_status_enum, nullable=False, default=RecordStatus.ACTIVE
    )


class EvalDataset(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "eval_datasets"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "type",
            "version",
            name="uq_eval_datasets_course_type_version",
        ),
        CheckConstraint("version > 0", name="version_positive"),
        Index("ix_eval_datasets_course_status", "course_id", "status"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    type: Mapped[EvalDatasetType] = mapped_column(
        eval_dataset_type_enum, nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[EvalDatasetStatus] = mapped_column(
        eval_dataset_status_enum,
        nullable=False,
        default=EvalDatasetStatus.DRAFT,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    frozen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class EvalCase(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "eval_cases"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id", "case_key", name="uq_eval_cases_dataset_case_key"
        ),
        Index("ix_eval_cases_dataset_status", "dataset_id", "status"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="CASCADE"), nullable=False
    )
    case_key: Mapped[str] = mapped_column(String(255), nullable=False)
    input: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    expected: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    labels: Mapped[dict[str, object]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    status: Mapped[RecordStatus] = mapped_column(
        record_status_enum, nullable=False, default=RecordStatus.ACTIVE
    )


class EvalRun(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "eval_runs"
    __table_args__ = (
        Index("ix_eval_runs_dataset_status", "dataset_id", "status"),
        Index("ix_eval_runs_trace_id", "trace_id"),
    )

    dataset_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("eval_datasets.id", ondelete="RESTRICT"), nullable=False
    )
    config: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[EvalRunStatus] = mapped_column(
        eval_run_status_enum, nullable=False, default=EvalRunStatus.QUEUED
    )
    metrics: Mapped[dict[str, object] | None] = mapped_column(JSON)
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    git_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    index_version: Mapped[int] = mapped_column(Integer, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class BadCase(UUIDPrimaryKeyMixin, TimestampedMixin, Base):
    __tablename__ = "bad_cases"
    __table_args__ = (
        UniqueConstraint(
            "course_id",
            "source_type",
            "source_id",
            "category",
            name="uq_bad_cases_course_source_category",
        ),
        Index("ix_bad_cases_course_status", "course_id", "status"),
        Index("ix_bad_cases_course_category", "course_id", "category"),
    )

    course_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    source_type: Mapped[BadCaseSourceType] = mapped_column(
        bad_case_source_type_enum, nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[BadCaseStatus] = mapped_column(
        bad_case_status_enum, nullable=False, default=BadCaseStatus.OPEN
    )
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reported_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
