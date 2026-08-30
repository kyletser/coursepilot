from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.models import CourseInvite, Enrollment, EnrollmentStatus
from app.security import hash_secret
from tests.helpers import (
    auth_headers,
    create_course,
    register_and_login,
)


async def test_teacher_create_course_hashes_invite_and_restricts_template(
    client, app_instance
):
    teacher, teacher_tokens = await register_and_login(
        client, "owner@example.com", role="TEACHER"
    )
    course = await create_course(client, teacher_tokens)
    assert course["owner_id"] == teacher["id"]
    assert course["template"] == "DATA_STRUCTURES"
    assert course["status"] == "ACTIVE"
    raw_invite = course["invite_code"]
    assert "code_hash" not in course

    async with app_instance.state.session_factory() as session:
        invite = await session.scalar(
            select(CourseInvite).where(
                CourseInvite.course_id == uuid.UUID(course["id"])
            )
        )
        assert invite is not None
        assert invite.code_hash == hash_secret(raw_invite)
        assert raw_invite != invite.code_hash

    bad_template = await client.post(
        "/api/v1/courses",
        headers=auth_headers(teacher_tokens),
        json={
            "template": "DATABASES",
            "code": "CS301",
            "name": "Databases",
            "semester": "2026-Fall",
        },
    )
    assert bad_template.status_code == 422
    assert bad_template.json()["error"]["code"] == "VALIDATION_ERROR"

    injected_owner = await client.post(
        "/api/v1/courses",
        headers=auth_headers(teacher_tokens),
        json={
            "template": "OPERATING_SYSTEMS",
            "code": "CS202",
            "name": "Operating Systems",
            "semester": "2026-Fall",
            "owner_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert injected_owner.status_code == 422


async def test_student_cannot_create_or_mutate_courses(client):
    _, student_tokens = await register_and_login(
        client, "student-role@example.com", role="STUDENT"
    )
    create = await client.post(
        "/api/v1/courses",
        headers=auth_headers(student_tokens),
        json={
            "template": "DATA_STRUCTURES",
            "code": "CS201",
            "name": "Data Structures",
            "semester": "2026-Fall",
        },
    )
    assert create.status_code == 403
    assert create.json()["error"]["code"] == "ROLE_FORBIDDEN"


async def test_join_is_idempotent_and_lists_only_active_enrollment(
    client, app_instance
):
    _, teacher_tokens = await register_and_login(
        client, "join-owner@example.com", role="TEACHER"
    )
    student, student_tokens = await register_and_login(
        client, "join-student@example.com", role="STUDENT"
    )
    course = await create_course(client, teacher_tokens)

    first = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    assert first.status_code == 201
    second = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    assert second.status_code == 200
    assert (
        second.json()["data"]["enrollment"]["id"]
        == first.json()["data"]["enrollment"]["id"]
    )

    courses = await client.get("/api/v1/courses", headers=auth_headers(student_tokens))
    assert [item["id"] for item in courses.json()["data"]] == [course["id"]]
    detail = await client.get(
        f"/api/v1/courses/{course['id']}", headers=auth_headers(student_tokens)
    )
    assert detail.status_code == 200

    async with app_instance.state.session_factory() as session:
        enrollment = await session.scalar(
            select(Enrollment).where(
                Enrollment.course_id == uuid.UUID(course["id"]),
                Enrollment.student_id == uuid.UUID(student["id"]),
            )
        )
        enrollment.status = EnrollmentStatus.LEFT
        await session.commit()

    assert (
        await client.get("/api/v1/courses", headers=auth_headers(student_tokens))
    ).json()["data"] == []
    denied = await client.get(
        f"/api/v1/courses/{course['id']}", headers=auth_headers(student_tokens)
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "COURSE_ACCESS_DENIED"


async def test_invite_reset_revokes_old_code_and_new_code_works(client):
    _, teacher_tokens = await register_and_login(
        client, "reset-owner@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "reset-student@example.com", role="STUDENT"
    )
    course = await create_course(client, teacher_tokens)
    old_code = course["invite_code"]

    reset = await client.post(
        f"/api/v1/courses/{course['id']}/invite/reset",
        headers=auth_headers(teacher_tokens),
    )
    assert reset.status_code == 200
    new_code = reset.json()["data"]["invite_code"]
    assert new_code != old_code

    old_join = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": old_code},
    )
    assert old_join.status_code == 410
    assert old_join.json()["error"]["code"] == "INVITE_REVOKED"

    new_join = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": new_code},
    )
    assert new_join.status_code == 201


async def test_invite_expiry_is_enforced(client, app_instance):
    _, teacher_tokens = await register_and_login(
        client, "expiry-owner@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "expiry-student@example.com", role="STUDENT"
    )
    course = await create_course(client, teacher_tokens)
    async with app_instance.state.session_factory() as session:
        invite = await session.scalar(
            select(CourseInvite).where(
                CourseInvite.course_id == uuid.UUID(course["id"])
            )
        )
        invite.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await session.commit()

    expired = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "INVITE_EXPIRED"


async def test_owner_and_cross_course_permissions_and_student_listing(client):
    _, owner_tokens = await register_and_login(
        client, "owner-one@example.com", role="TEACHER"
    )
    _, other_teacher_tokens = await register_and_login(
        client, "owner-two@example.com", role="TEACHER"
    )
    student, student_tokens = await register_and_login(
        client, "listed-student@example.com", role="STUDENT"
    )
    course = await create_course(client, owner_tokens)
    other_course = await create_course(
        client, other_teacher_tokens, code="CS202", template="OPERATING_SYSTEMS"
    )
    await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )

    denied = await client.get(
        f"/api/v1/courses/{course['id']}",
        headers=auth_headers(other_teacher_tokens),
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "COURSE_ACCESS_DENIED"

    cross_course = await client.get(
        f"/api/v1/courses/{other_course['id']}",
        headers=auth_headers(student_tokens),
    )
    assert cross_course.status_code == 403

    student_forbidden = await client.get(
        f"/api/v1/courses/{course['id']}/students",
        headers=auth_headers(student_tokens),
    )
    assert student_forbidden.status_code == 403

    roster = await client.get(
        f"/api/v1/courses/{course['id']}/students",
        headers=auth_headers(owner_tokens),
    )
    assert roster.status_code == 200
    assert [entry["id"] for entry in roster.json()["data"]] == [student["id"]]
    assert "password_hash" not in roster.text

    other_roster = await client.get(
        f"/api/v1/courses/{course['id']}/students",
        headers=auth_headers(other_teacher_tokens),
    )
    assert other_roster.status_code == 403


async def test_archived_course_disappears_and_denies_student_access(client):
    _, teacher_tokens = await register_and_login(
        client, "archive-owner@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "archive-student@example.com", role="STUDENT"
    )
    course = await create_course(client, teacher_tokens)
    await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    archived = await client.patch(
        f"/api/v1/courses/{course['id']}",
        headers=auth_headers(teacher_tokens),
        json={"status": "ARCHIVED"},
    )
    assert archived.status_code == 200

    listing = await client.get("/api/v1/courses", headers=auth_headers(student_tokens))
    assert listing.json()["data"] == []
    detail = await client.get(
        f"/api/v1/courses/{course['id']}", headers=auth_headers(student_tokens)
    )
    assert detail.status_code == 403

    rejoin = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    assert rejoin.status_code == 409
    assert rejoin.json()["error"]["code"] == "COURSE_ARCHIVED"
