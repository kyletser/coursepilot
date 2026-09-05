from __future__ import annotations

import asyncio
import threading
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.ingestion.pipeline import (
    DocumentEmbeddingAdapter,
    run_ingestion_pipeline,
)
from app.retrieval import BGEM3EmbeddingAdapter
from app.worker import celery_app

_embedding_adapter: DocumentEmbeddingAdapter | None = None
_embedding_lock = threading.Lock()


def shared_embedding_adapter(settings: Settings) -> DocumentEmbeddingAdapter:
    """One embedding adapter per worker process.

    Loading BGE-M3 costs roughly 2 GB of memory and tens of seconds, so
    Celery tasks must reuse a process-level singleton instead of reloading
    the model for every job. The adapter stays lazy: nothing is loaded until
    the first embed call, which also keeps weights out of the prefork parent.
    """

    global _embedding_adapter
    adapter = _embedding_adapter
    if adapter is None:
        with _embedding_lock:
            if _embedding_adapter is None:
                _embedding_adapter = BGEM3EmbeddingAdapter(
                    settings.embedding_model,
                    allow_download=settings.model_allow_download,
                    cache_folder=settings.hf_hub_cache,
                )
            adapter = _embedding_adapter
    return adapter


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
            embedding_adapter=embedding_adapter
            or shared_embedding_adapter(resolved_settings),
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
