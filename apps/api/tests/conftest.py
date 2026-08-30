from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio

from app.config import Settings
from app.db import create_engine
from app.main import create_app
from app.models import Base


@pytest_asyncio.fixture
async def app_instance(tmp_path):
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="coursepilot-test-secret-at-least-32-bytes",
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
    engine = create_engine(settings)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    application = create_app(settings, engine=engine)
    yield application
    await engine.dispose()


@pytest_asyncio.fixture
async def client(app_instance) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app_instance, raise_app_exceptions=False)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as test_client:
        yield test_client
