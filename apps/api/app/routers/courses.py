from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.dependencies import (
    CurrentUser,
    SessionDep,
    StudentUser,
    TeacherUser,
)
from app.errors import AppError, success_response
from app.models import (
    Course,
    CourseInvite,
    CourseStatus,
    Enrollment,
    EnrollmentStatus,
    User,
    UserRole,
)
from app.schemas import (
    CourseCreatedResponse,
    CourseCreateRequest,
    CourseResponse,
    CourseUpdateRequest,
    EnrolledStudentResponse,
    EnrollmentResponse,
    InviteResetResponse,
    JoinCourseRequest,
    JoinedCourseResponse,
)
from app.security import ensure_aware, hash_secret

router = APIRouter(prefix="/api/v1/courses", tags=["courses"])


def _create_invite(request: Request, course_id: uuid.UUID) -> tuple[str, CourseInvite]:
    plaintext = f"CP-{secrets.token_urlsafe(16)}"
    expires_at = datetime.now(UTC) + timedelta(
        days=request.app.state.settings.invite_ttl_days
    )
    return plaintext, CourseInvite(
        course_id=course_id,
        code_hash=hash_secret(plaintext),
        expires_at=expires_at,
    )


async def _owner_course(
    session: SessionDep,
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


async def _authorized_course(
    session: SessionDep, course_id: uuid.UUID, user: User
) -> Course:
    course = await session.scalar(select(Course).where(Course.id == course_id))
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
    enrollment = await session.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == course.id,
            Enrollment.student_id == user.id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
    )
    if enrollment is None:
        raise AppError(
            403,
            "COURSE_ACCESS_DENIED",
            "You do not have access to this course",
        )
    return course


@router.post("", status_code=201)
async def create_course(
    payload: CourseCreateRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    course = Course(
        owner_id=teacher.id,
        template=payload.template,
        code=payload.code,
        name=payload.name,
        description=payload.description,
        semester=payload.semester,
    )
    session.add(course)
    await session.flush()
    plaintext, invite = _create_invite(request, course.id)
    session.add(invite)
    await session.commit()
    await session.refresh(course)
    data = CourseCreatedResponse(
        **CourseResponse.model_validate(course).model_dump(),
        invite_code=plaintext,
        invite_expires_at=invite.expires_at,
    )
    return success_response(request, data, status_code=201)


@router.post("/join")
async def join_course(
    payload: JoinCourseRequest,
    request: Request,
    session: SessionDep,
    student: StudentUser,
):
    invite = await session.scalar(
        select(CourseInvite)
        .where(CourseInvite.code_hash == hash_secret(payload.invite_code))
        .with_for_update()
    )
    if invite is None:
        raise AppError(404, "INVITE_INVALID", "The course invite is invalid")
    if invite.revoked_at is not None:
        raise AppError(410, "INVITE_REVOKED", "The course invite was revoked")
    if ensure_aware(invite.expires_at) <= datetime.now(UTC):
        raise AppError(410, "INVITE_EXPIRED", "The course invite expired")

    course = await session.scalar(select(Course).where(Course.id == invite.course_id))
    if course is None:
        raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
    if course.status != CourseStatus.ACTIVE:
        raise AppError(409, "COURSE_ARCHIVED", "Archived courses cannot be joined")

    target_course_id = course.id
    target_student_id = student.id

    enrollment = await session.scalar(
        select(Enrollment).where(
            Enrollment.course_id == target_course_id,
            Enrollment.student_id == target_student_id,
        )
    )
    created = enrollment is None
    if enrollment is None:
        enrollment = Enrollment(
            course_id=target_course_id, student_id=target_student_id
        )
        session.add(enrollment)
    elif enrollment.status != EnrollmentStatus.ACTIVE:
        enrollment.status = EnrollmentStatus.ACTIVE
        enrollment.joined_at = datetime.now(UTC)
    try:
        await session.commit()
    except IntegrityError:
        # Another identical join won the unique(course_id, student_id) race.
        await session.rollback()
        enrollment = await session.scalar(
            select(Enrollment).where(
                Enrollment.course_id == target_course_id,
                Enrollment.student_id == target_student_id,
            )
        )
        if enrollment is None:
            raise
        course = await session.scalar(
            select(Course).where(Course.id == target_course_id)
        )
        if course is None:
            raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
        created = False
    if created:
        await session.refresh(enrollment)
    data = JoinedCourseResponse(
        enrollment=EnrollmentResponse.model_validate(enrollment),
        course=CourseResponse.model_validate(course),
    )
    return success_response(request, data, status_code=201 if created else 200)


@router.get("")
async def list_courses(
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
):
    if current_user.role == UserRole.TEACHER:
        statement = select(Course).where(Course.owner_id == current_user.id)
    else:
        statement = (
            select(Course)
            .join(Enrollment, Enrollment.course_id == Course.id)
            .where(
                Enrollment.student_id == current_user.id,
                Enrollment.status == EnrollmentStatus.ACTIVE,
                Course.status == CourseStatus.ACTIVE,
            )
        )
    courses = (
        await session.scalars(statement.order_by(Course.created_at.desc()))
    ).all()
    return success_response(
        request, [CourseResponse.model_validate(course) for course in courses]
    )


@router.get("/{course_id}")
async def get_course(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
):
    course = await _authorized_course(session, course_id, current_user)
    return success_response(request, CourseResponse.model_validate(course))


@router.patch("/{course_id}")
async def update_course(
    course_id: uuid.UUID,
    payload: CourseUpdateRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    course = await _owner_course(session, course_id, teacher, for_update=True)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(course, field, value)
    await session.commit()
    await session.refresh(course)
    return success_response(request, CourseResponse.model_validate(course))


@router.post("/{course_id}/invite/reset")
async def reset_invite(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    course = await _owner_course(session, course_id, teacher, for_update=True)
    now = datetime.now(UTC)
    await session.execute(
        update(CourseInvite)
        .where(
            CourseInvite.course_id == course.id,
            CourseInvite.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )
    plaintext, invite = _create_invite(request, course.id)
    session.add(invite)
    await session.commit()
    return success_response(
        request,
        InviteResetResponse(
            course_id=course.id,
            invite_code=plaintext,
            expires_at=invite.expires_at,
        ),
    )


@router.get("/{course_id}/students")
async def list_students(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    await _owner_course(session, course_id, teacher)
    rows = (
        await session.execute(
            select(User, Enrollment)
            .join(Enrollment, Enrollment.student_id == User.id)
            .where(
                Enrollment.course_id == course_id,
                Enrollment.status == EnrollmentStatus.ACTIVE,
                User.role == UserRole.STUDENT,
            )
            .order_by(Enrollment.joined_at.asc())
        )
    ).all()
    data = [
        EnrolledStudentResponse(
            id=user.id,
            email=user.email,
            status=user.status,
            enrollment_id=enrollment.id,
            enrollment_status=enrollment.status,
            joined_at=enrollment.joined_at,
        )
        for user, enrollment in rows
    ]
    return success_response(request, data)
