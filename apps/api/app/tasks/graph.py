from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from neo4j import GraphDatabase
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.graph.neo4j import Neo4jGraphConsumer
from app.graph.outbox import GraphEventType, GraphOutboxEvent
from app.models import GraphOutbox, GraphOutboxStatus
from app.worker import celery_app

MAX_OUTBOX_ATTEMPTS = 5
OUTBOX_DISPATCH_BATCH_SIZE = 100
OUTBOX_PROCESSING_LEASE = timedelta(minutes=2)
OUTBOX_DISPATCH_RETRY_DELAY = timedelta(seconds=10)


async def dispatch_graph_outbox_events(
    *,
    session_factory: async_sessionmaker | None = None,
    settings: Settings | None = None,
    enqueue: Callable[[str], object] | None = None,
    now: datetime | None = None,
    batch_size: int = OUTBOX_DISPATCH_BATCH_SIZE,
) -> dict[str, Any]:
    """Lease due outbox facts and enqueue their idempotent Neo4j deliveries."""

    if batch_size < 1:
        raise ValueError("batch_size must be positive")

    owned_engine = None
    if session_factory is None:
        actual_settings = settings or get_settings()
        owned_engine = create_engine(actual_settings)
        session_factory = create_session_factory(owned_engine)

    dispatch_now = (now or datetime.now(UTC)).astimezone(UTC)
    stale_before = dispatch_now - OUTBOX_PROCESSING_LEASE
    lease_until = dispatch_now + OUTBOX_PROCESSING_LEASE
    enqueue_delivery = enqueue or _enqueue_graph_outbox_delivery

    try:
        async with session_factory() as session:
            records = (
                await session.scalars(
                    select(GraphOutbox)
                    .where(
                        or_(
                            and_(
                                GraphOutbox.status.in_(
                                    (
                                        GraphOutboxStatus.PENDING,
                                        GraphOutboxStatus.FAILED,
                                    )
                                ),
                                GraphOutbox.available_at <= dispatch_now,
                            ),
                            and_(
                                GraphOutbox.status == GraphOutboxStatus.PROCESSING,
                                GraphOutbox.updated_at <= stale_before,
                            ),
                        )
                    )
                    .order_by(
                        GraphOutbox.available_at.asc(), GraphOutbox.created_at.asc()
                    )
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).all()
            stale_ids = {
                record.id
                for record in records
                if record.status == GraphOutboxStatus.PROCESSING
            }
            event_ids = [record.id for record in records]
            for record in records:
                record.status = GraphOutboxStatus.PROCESSING
                record.available_at = lease_until
                record.last_error = None
            await session.commit()

        enqueued = 0
        failed = 0
        for event_id in event_ids:
            try:
                enqueue_delivery(str(event_id))
            except Exception as exc:  # noqa: BLE001 - broker errors must not lose facts
                failed += 1
                async with session_factory() as session:
                    record = await session.scalar(
                        select(GraphOutbox)
                        .where(GraphOutbox.id == event_id)
                        .with_for_update()
                    )
                    if (
                        record is not None
                        and record.status == GraphOutboxStatus.PROCESSING
                    ):
                        record.status = GraphOutboxStatus.FAILED
                        record.available_at = (
                            datetime.now(UTC) + OUTBOX_DISPATCH_RETRY_DELAY
                        )
                        record.last_error = (
                            f"DISPATCH_{type(exc).__name__}: broker enqueue failed"
                        )
                        await session.commit()
            else:
                enqueued += 1

        return {
            "selected": len(event_ids),
            "enqueued": enqueued,
            "failed": failed,
            "recovered_stale": len(stale_ids),
        }
    finally:
        if owned_engine is not None:
            await owned_engine.dispose()


def _enqueue_graph_outbox_delivery(event_id: str) -> object:
    return deliver_graph_outbox_event_task.apply_async(
        args=[event_id],
        expires=int(OUTBOX_PROCESSING_LEASE.total_seconds()),
    )


async def deliver_graph_outbox_event(
    event_id: uuid.UUID | str,
    *,
    session_factory: async_sessionmaker | None = None,
    consumer: Any | None = None,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Claim one outbox row and deliver it through Neo4jGraphConsumer."""

    parsed_event_id = (
        event_id if isinstance(event_id, uuid.UUID) else uuid.UUID(str(event_id))
    )
    owned_engine = None
    if session_factory is None:
        actual_settings = settings or get_settings()
        owned_engine = create_engine(actual_settings)
        session_factory = create_session_factory(owned_engine)
    else:
        actual_settings = settings or get_settings()

    owned_driver = None
    try:
        async with session_factory() as session:
            record = await session.scalar(
                select(GraphOutbox)
                .where(GraphOutbox.id == parsed_event_id)
                .with_for_update()
            )
            if record is None:
                return {"event_id": str(parsed_event_id), "status": "NOT_FOUND"}
            if record.status == GraphOutboxStatus.SUCCEEDED:
                return {"event_id": str(record.id), "status": "SUCCEEDED"}
            if record.status == GraphOutboxStatus.DEAD_LETTER:
                return {"event_id": str(record.id), "status": "DEAD_LETTER"}
            now = datetime.now(UTC)
            if (
                record.status != GraphOutboxStatus.PROCESSING
                and record.available_at > now
            ):
                return {"event_id": str(record.id), "status": "DEFERRED"}

            event = GraphOutboxEvent(
                id=record.id,
                event_type=GraphEventType(record.event_type),
                aggregate_id=record.aggregate_id,
                payload=dict(record.payload),
                occurred_at=record.created_at,
            )
            record.status = GraphOutboxStatus.PROCESSING
            record.available_at = now + OUTBOX_PROCESSING_LEASE
            record.last_error = None
            await session.commit()

        if consumer is None:
            owned_driver = GraphDatabase.driver(
                actual_settings.neo4j_uri,
                auth=(
                    actual_settings.neo4j_username,
                    actual_settings.neo4j_password,
                ),
            )
            consumer = Neo4jGraphConsumer(owned_driver)

        try:
            await asyncio.to_thread(consumer.consume, event)
        except Exception as exc:
            async with session_factory() as session:
                failed = await session.scalar(
                    select(GraphOutbox)
                    .where(GraphOutbox.id == parsed_event_id)
                    .with_for_update()
                )
                if failed is not None and failed.status != GraphOutboxStatus.SUCCEEDED:
                    failed.retry_count += 1
                    failed.last_error = f"{type(exc).__name__}: {exc}"[:4000]
                    if failed.retry_count >= MAX_OUTBOX_ATTEMPTS:
                        failed.status = GraphOutboxStatus.DEAD_LETTER
                    else:
                        failed.status = GraphOutboxStatus.FAILED
                        failed.available_at = datetime.now(UTC) + timedelta(
                            seconds=min(300, 2**failed.retry_count)
                        )
                    await session.commit()
            raise

        processed_at = datetime.now(UTC)
        async with session_factory() as session:
            delivered = await session.scalar(
                select(GraphOutbox)
                .where(GraphOutbox.id == parsed_event_id)
                .with_for_update()
            )
            if delivered is not None:
                delivered.status = GraphOutboxStatus.SUCCEEDED
                delivered.processed_at = processed_at
                delivered.last_error = None
                await session.commit()
        return {
            "event_id": str(parsed_event_id),
            "status": "SUCCEEDED",
            "processed_at": processed_at.isoformat(),
        }
    finally:
        if owned_driver is not None:
            owned_driver.close()
        if owned_engine is not None:
            await owned_engine.dispose()


@celery_app.task(
    name="coursepilot.graph.deliver_outbox_event",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": MAX_OUTBOX_ATTEMPTS},
)
def deliver_graph_outbox_event_task(event_id: str) -> dict[str, Any]:
    return asyncio.run(deliver_graph_outbox_event(event_id))


@celery_app.task(
    name="coursepilot.graph.dispatch_outbox",
    ignore_result=True,
)
def dispatch_graph_outbox_events_task() -> dict[str, Any]:
    return asyncio.run(dispatch_graph_outbox_events())
