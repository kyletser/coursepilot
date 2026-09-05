from __future__ import annotations

from celery import Celery

from app.config import get_settings

settings = get_settings()
celery_app = Celery(
    "coursepilot",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.ingestion", "app.tasks.graph", "app.tasks.evaluation"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 900},
    worker_prefetch_multiplier=1,
    beat_schedule={
        "dispatch-graph-outbox": {
            "task": "coursepilot.graph.dispatch_outbox",
            "schedule": 5.0,
            "options": {"expires": 10},
        }
    },
)


@celery_app.task(name="coursepilot.ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}
