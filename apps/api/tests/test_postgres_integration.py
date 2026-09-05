from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.main import create_app
from app.models import Base
from tests.helpers import auth_headers, create_course, register_and_login


def _asyncpg_url(value: str) -> str:
    if value.startswith("postgresql+asyncpg://"):
        return value
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    raise ValueError("COURSEPILOT_POSTGRES_TEST_URL must be a PostgreSQL URL")


@pytest_asyncio.fixture
async def postgres_client(tmp_path) -> AsyncIterator[httpx.AsyncClient]:
    raw_url = os.getenv("COURSEPILOT_POSTGRES_TEST_URL")
    if not raw_url:
        pytest.skip("COURSEPILOT_POSTGRES_TEST_URL is not configured")
    database_url = _asyncpg_url(raw_url)
    schema = f"cp_test_{uuid.uuid4().hex}"
    admin_engine = create_async_engine(database_url)
    async with admin_engine.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        # Keep test tables isolated while retaining access to extensions such
        # as pgvector, which migrations install in the public schema.
        connect_args={"server_settings": {"search_path": f"{schema},public"}},
    )
    try:
        async with engine.begin() as connection:
            # The public schema contains the live application's tables as well as
            # the pgvector extension. With checkfirst=True, PostgreSQL can report
            # the public tables as existing through search_path and SQLAlchemy then
            # skips creating isolated copies in this fresh test schema.
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(
                    sync_connection, checkfirst=False
                )
            )
        settings = Settings(
            _env_file=None,  # type: ignore[call-arg]
            environment="test",
            database_url=database_url,
            jwt_secret="coursepilot-postgres-test-secret-32-bytes",
            argon2_time_cost=1,
            argon2_memory_cost=8192,
            argon2_parallelism=1,
            readiness_check_migrations=False,
            readiness_check_redis=False,
            readiness_check_neo4j=False,
            readiness_check_llm=False,
            readiness_check_index=False,
            index_root=tmp_path,
        )
        application = create_app(settings, engine=engine)
        transport = httpx.ASGITransport(app=application, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://postgres-test"
        ) as client:
            yield client
    finally:
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin_engine.dispose()


@pytest.mark.postgres
async def test_postgres_identity_course_and_tenant_permissions(postgres_client):
    client = postgres_client
    _, owner_tokens = await register_and_login(
        client, "pg-owner@example.com", role="TEACHER"
    )
    _, other_teacher_tokens = await register_and_login(
        client, "pg-other@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "pg-student@example.com", role="STUDENT"
    )
    course = await create_course(client, owner_tokens)

    first_join = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    second_join = await client.post(
        "/api/v1/courses/join",
        headers=auth_headers(student_tokens),
        json={"invite_code": course["invite_code"]},
    )
    assert first_join.status_code == 201
    assert second_join.status_code == 200
    assert (
        first_join.json()["data"]["enrollment"]["id"]
        == second_join.json()["data"]["enrollment"]["id"]
    )

    student_courses = await client.get(
        "/api/v1/courses", headers=auth_headers(student_tokens)
    )
    assert [item["id"] for item in student_courses.json()["data"]] == [course["id"]]

    owner_courses = await client.get(
        "/api/v1/courses", headers=auth_headers(owner_tokens)
    )
    assert [item["id"] for item in owner_courses.json()["data"]] == [course["id"]]

    cross_teacher = await client.get(
        f"/api/v1/courses/{course['id']}",
        headers=auth_headers(other_teacher_tokens),
    )
    assert cross_teacher.status_code == 403
    assert cross_teacher.json()["error"]["code"] == "COURSE_ACCESS_DENIED"

    roster = await client.get(
        f"/api/v1/courses/{course['id']}/students",
        headers=auth_headers(owner_tokens),
    )
    assert roster.status_code == 200
    assert [item["email"] for item in roster.json()["data"]] == [
        "pg-student@example.com"
    ]
