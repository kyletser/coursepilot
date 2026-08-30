from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.graph.outbox import GraphEventType
from app.models import (
    Course,
    CourseTemplate,
    GraphOutbox,
    GraphOutboxStatus,
    User,
    UserRole,
)
from app.tasks.graph import (
    OUTBOX_DISPATCH_RETRY_DELAY,
    OUTBOX_PROCESSING_LEASE,
    deliver_graph_outbox_event,
    dispatch_graph_outbox_events,
)
from app.worker import celery_app


async def _seed_course(app_instance) -> uuid.UUID:
    async with app_instance.state.session_factory() as session:
        teacher = User(
            email=f"outbox-{uuid.uuid4()}@example.com",
            password_hash="test-only",
            role=UserRole.TEACHER,
        )
        session.add(teacher)
        await session.flush()
        course = Course(
            owner_id=teacher.id,
            template=CourseTemplate.DATA_STRUCTURES,
            code=f"OUTBOX-{uuid.uuid4().hex[:8]}",
            name="Outbox test",
            description="",
            semester="2026",
        )
        session.add(course)
        await session.commit()
        return course.id


def _outbox(
    course_id: uuid.UUID,
    *,
    status: GraphOutboxStatus,
    available_at: datetime,
    updated_at: datetime,
) -> GraphOutbox:
    aggregate_id = uuid.uuid4()
    return GraphOutbox(
        course_id=course_id,
        event_type=GraphEventType.CONCEPT_APPROVED.value,
        aggregate_id=aggregate_id,
        deduplication_key=f"test:{aggregate_id}",
        payload={
            "schema_version": 1,
            "fact_source": "POSTGRESQL",
            "concept_id": str(aggregate_id),
            "course_id": str(course_id),
            "name": "Stacks",
            "description": "LIFO",
            "source_chunk_id": str(uuid.uuid4()),
            "confidence": 0.9,
            "status": "APPROVED",
            "reviewer_id": str(uuid.uuid4()),
            "reviewed_at": updated_at.isoformat(),
        },
        status=status,
        available_at=available_at,
        updated_at=updated_at,
    )


async def test_dispatch_selects_due_records_and_recovers_stale_processing(
    app_instance,
):
    course_id = await _seed_course(app_instance)
    dispatch_now = datetime.now(UTC)
    stale_at = dispatch_now - OUTBOX_PROCESSING_LEASE - timedelta(seconds=1)
    future = dispatch_now + timedelta(hours=1)

    async with app_instance.state.session_factory() as session:
        records = {
            "pending": _outbox(
                course_id,
                status=GraphOutboxStatus.PENDING,
                available_at=dispatch_now - timedelta(seconds=1),
                updated_at=dispatch_now,
            ),
            "failed": _outbox(
                course_id,
                status=GraphOutboxStatus.FAILED,
                available_at=dispatch_now,
                updated_at=dispatch_now,
            ),
            "stale": _outbox(
                course_id,
                status=GraphOutboxStatus.PROCESSING,
                available_at=future,
                updated_at=stale_at,
            ),
            "future": _outbox(
                course_id,
                status=GraphOutboxStatus.FAILED,
                available_at=future,
                updated_at=dispatch_now,
            ),
            "active": _outbox(
                course_id,
                status=GraphOutboxStatus.PROCESSING,
                available_at=future,
                updated_at=dispatch_now,
            ),
            "done": _outbox(
                course_id,
                status=GraphOutboxStatus.SUCCEEDED,
                available_at=dispatch_now,
                updated_at=stale_at,
            ),
        }
        session.add_all(records.values())
        await session.commit()
        ids = {name: record.id for name, record in records.items()}

    enqueued: list[str] = []
    result = await dispatch_graph_outbox_events(
        session_factory=app_instance.state.session_factory,
        enqueue=enqueued.append,
        now=dispatch_now,
    )

    expected = {ids["pending"], ids["failed"], ids["stale"]}
    assert result == {
        "selected": 3,
        "enqueued": 3,
        "failed": 0,
        "recovered_stale": 1,
    }
    assert {uuid.UUID(event_id) for event_id in enqueued} == expected

    async with app_instance.state.session_factory() as session:
        stored = {
            record.id: record
            for record in (await session.scalars(select(GraphOutbox))).all()
        }
    for event_id in expected:
        assert stored[event_id].status == GraphOutboxStatus.PROCESSING
        assert stored[event_id].available_at == dispatch_now + OUTBOX_PROCESSING_LEASE
    assert stored[ids["future"]].status == GraphOutboxStatus.FAILED
    assert stored[ids["active"]].status == GraphOutboxStatus.PROCESSING
    assert stored[ids["done"]].status == GraphOutboxStatus.SUCCEEDED


class _RecordingConsumer:
    def __init__(self) -> None:
        self.event_ids: list[uuid.UUID] = []

    def consume(self, event) -> None:
        self.event_ids.append(event.id)


async def test_enqueue_failure_stays_recoverable_and_claimed_event_is_delivered(
    app_instance,
):
    course_id = await _seed_course(app_instance)
    dispatch_now = datetime.now(UTC)
    async with app_instance.state.session_factory() as session:
        event = _outbox(
            course_id,
            status=GraphOutboxStatus.PENDING,
            available_at=dispatch_now,
            updated_at=dispatch_now,
        )
        session.add(event)
        await session.commit()
        event_id = event.id

    def unavailable_broker(_event_id: str) -> None:
        raise ConnectionError("redis://must-not-be-persisted")

    failed = await dispatch_graph_outbox_events(
        session_factory=app_instance.state.session_factory,
        enqueue=unavailable_broker,
        now=dispatch_now,
    )
    assert failed == {
        "selected": 1,
        "enqueued": 0,
        "failed": 1,
        "recovered_stale": 0,
    }

    async with app_instance.state.session_factory() as session:
        stored = await session.get(GraphOutbox, event_id)
        assert stored is not None
        assert stored.status == GraphOutboxStatus.FAILED
        assert stored.retry_count == 0
        assert stored.available_at <= datetime.now(UTC) + OUTBOX_DISPATCH_RETRY_DELAY
        assert stored.last_error == ("DISPATCH_ConnectionError: broker enqueue failed")
        retry_at = stored.available_at

    enqueued: list[str] = []
    retried = await dispatch_graph_outbox_events(
        session_factory=app_instance.state.session_factory,
        enqueue=enqueued.append,
        now=retry_at + timedelta(microseconds=1),
    )
    assert retried["enqueued"] == 1
    assert enqueued == [str(event_id)]

    consumer = _RecordingConsumer()
    delivered = await deliver_graph_outbox_event(
        event_id,
        session_factory=app_instance.state.session_factory,
        consumer=consumer,
        settings=app_instance.state.settings,
    )
    assert delivered["status"] == "SUCCEEDED"
    assert consumer.event_ids == [event_id]


def test_graph_dispatcher_is_registered_with_periodic_schedule():
    schedule = celery_app.conf.beat_schedule["dispatch-graph-outbox"]
    assert schedule["task"] == "coursepilot.graph.dispatch_outbox"
    assert schedule["schedule"] == 5.0
