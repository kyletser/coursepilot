from __future__ import annotations

import asyncio
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.ingestion.pipeline import (
    DocumentEmbeddingAdapter,
    run_ingestion_pipeline,
)
from app.worker import celery_app


async def run_ingestion_job(
    job_id: uuid.UUID | str,
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    embedding_adapter: DocumentEmbeddingAdapter | None = None,
) -> dict[str, object]:
    """Async worker entry with injectable persistence/model dependencies."""

    resolved_settings = settings or get_settings()
    engine = None
    if session_factory is None:
        engine = create_engine(resolved_settings)
        session_factory = create_session_factory(engine)
    try:
        return await run_ingestion_pipeline(
            job_id,
            session_factory=session_factory,
            settings=resolved_settings,
            embedding_adapter=embedding_adapter,
        )
    finally:
        if engine is not None:
            await engine.dispose()


def run_ingestion_job_sync(job_id: uuid.UUID | str) -> dict[str, object]:
    """Celery's prefork workers are synchronous; own one event loop per task."""

    return asyncio.run(run_ingestion_job(job_id))


@celery_app.task(
    name="coursepilot.ingestion.process",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_ingestion_job(job_id: str) -> dict[str, object]:
    return run_ingestion_job_sync(job_id)


def enqueue_ingestion_job(job_id: uuid.UUID | str) -> None:
    process_ingestion_job.apply_async(args=[str(job_id)], retry=False)
