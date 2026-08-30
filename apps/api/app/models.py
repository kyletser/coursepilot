from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
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
