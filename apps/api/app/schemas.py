from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models import (
    CourseStatus,
    CourseTemplate,
    EnrollmentStatus,
    UserRole,
    UserStatus,
)


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ApiEnvelope(BaseModel):
    data: Any | None
    error: ErrorBody | None
    request_id: str


class RegisterRequest(RequestModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: UserRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().casefold()


class LoginRequest(RequestModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().casefold()


class RefreshRequest(RequestModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class LogoutRequest(RequestModel):
    refresh_token: str = Field(min_length=32, max_length=512)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    role: UserRole
    status: UserStatus
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    refresh_expires_in: int


class CourseCreateRequest(RequestModel):
    template: CourseTemplate
    code: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    semester: str = Field(min_length=1, max_length=64)

    @field_validator("code", "name", "semester")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return value.upper()


class CourseUpdateRequest(RequestModel):
    code: str | None = Field(default=None, min_length=1, max_length=64)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10000)
    semester: str | None = Field(default=None, min_length=1, max_length=64)
    status: CourseStatus | None = None

    @field_validator("code", "name", "semester")
    @classmethod
    def strip_optional_nonempty(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("code")
    @classmethod
    def normalize_optional_code(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class CourseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_id: uuid.UUID
    template: CourseTemplate
    code: str
    name: str
    description: str
    semester: str
    status: CourseStatus
    created_at: datetime
    updated_at: datetime


class CourseCreatedResponse(CourseResponse):
    invite_code: str
    invite_expires_at: datetime


class InviteResetResponse(BaseModel):
    course_id: uuid.UUID
    invite_code: str
    expires_at: datetime


class JoinCourseRequest(RequestModel):
    invite_code: str = Field(min_length=8, max_length=256)

    @field_validator("invite_code")
    @classmethod
    def strip_invite(cls, value: str) -> str:
        return value.strip()


class EnrollmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    student_id: uuid.UUID
    status: EnrollmentStatus
    joined_at: datetime


class JoinedCourseResponse(BaseModel):
    enrollment: EnrollmentResponse
    course: CourseResponse


class EnrolledStudentResponse(BaseModel):
    id: uuid.UUID
    email: EmailStr
    status: UserStatus
    enrollment_id: uuid.UUID
    enrollment_status: EnrollmentStatus
    joined_at: datetime
