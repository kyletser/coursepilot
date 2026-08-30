from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from neo4j import AsyncGraphDatabase
from redis.asyncio import Redis
from sqlalchemy import text

from app.errors import AppError, success_response

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/live")
async def live(request: Request):
    return success_response(request, {"status": "ok"})


async def _database_check(request: Request) -> None:
    async with request.app.state.db_engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
        if request.app.state.settings.readiness_check_migrations:
            revision = await connection.scalar(
                text("SELECT version_num FROM alembic_version")
            )
            if revision != request.app.state.required_db_revision:
                raise RuntimeError("database schema is not at the required revision")


async def _redis_check(request: Request) -> None:
    client = Redis.from_url(request.app.state.settings.redis_url)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _neo4j_check(request: Request) -> None:
    settings = request.app.state.settings
    driver = AsyncGraphDatabase.driver(
        settings.neo4j_uri,
        auth=(settings.neo4j_username, settings.neo4j_password),
    )
    try:
        await driver.verify_connectivity()
    finally:
        await driver.close()


async def _llm_check(request: Request) -> None:
    settings = request.app.state.settings
    headers = {"Authorization": f"Bearer {settings.llm_api_key}"}
    async with httpx.AsyncClient(timeout=settings.readiness_timeout_seconds) as client:
        response = await client.get(
            f"{settings.llm_base_url.rstrip('/')}/models", headers=headers
        )
        response.raise_for_status()


def _check_index_path(path: Path) -> None:
    if not path.is_dir():
        raise FileNotFoundError("index root is not a directory")
    if not os.access(path, os.R_OK | os.W_OK):
        raise PermissionError("index root is not readable and writable")


async def _index_check(request: Request) -> None:
    await asyncio.to_thread(_check_index_path, request.app.state.settings.index_root)


async def _run_check(
    name: str,
    checker: Callable[[Request], Awaitable[None]],
    request: Request,
) -> tuple[str, dict[str, Any]]:
    try:
        await asyncio.wait_for(
            checker(request),
            timeout=request.app.state.settings.readiness_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - aggregate dependency failures as readiness data
        return name, {"status": "error", "reason": type(exc).__name__}
    return name, {"status": "ok"}


@router.get("/ready")
async def ready(request: Request):
    settings = request.app.state.settings
    enabled: list[tuple[str, Callable[[Request], Awaitable[None]]]] = [
        ("database", _database_check)
    ]
    skipped: dict[str, dict[str, str]] = {}
    for name, is_enabled, checker in (
        ("redis", settings.readiness_check_redis, _redis_check),
        ("neo4j", settings.readiness_check_neo4j, _neo4j_check),
        ("model_adapter", settings.readiness_check_llm, _llm_check),
        ("index_cache", settings.readiness_check_index, _index_check),
    ):
        if is_enabled:
            enabled.append((name, checker))
        else:
            skipped[name] = {"status": "skipped"}

    results = await asyncio.gather(
        *(_run_check(name, checker, request) for name, checker in enabled)
    )
    components = dict(results) | skipped
    failed = [name for name, result in results if result["status"] != "ok"]
    if failed:
        raise AppError(
            503,
            "SERVICE_NOT_READY",
            "One or more required dependencies are unavailable",
            details={"components": components, "failed": failed},
        )
    return success_response(request, {"status": "ready", "components": components})
