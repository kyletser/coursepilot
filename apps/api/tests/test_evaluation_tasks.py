from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.evaluation import service
from app.models import (
    Course,
    CourseIndex,
    CourseIndexStatus,
    CourseTemplate,
    EvalCase,
    EvalDataset,
    EvalDatasetStatus,
    EvalDatasetType,
    EvalRun,
    EvalRunStatus,
    IndexComponentStatus,
    User,
    UserRole,
)
from app.tasks.evaluation import run_eval_run_job
from tests.helpers import create_course, register_and_login
from tests.test_evaluation_api import _dataset_with_case, _freeze_and_run

LAUNCH_CONFIG = {
    "index_version": 1,
    "report_version": None,
    "prompt_version": None,
    "route_top_k": 20,
    "fusion_top_k": 20,
    "final_top_k": 10,
    "rrf_k": 60,
    "reranker_timeout_seconds": 15.0,
    "graph_depth": 2,
    "kg_weight": 1.0,
}


async def _seed_queued_run(app_instance, *, case_input: dict):
    now = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
    async with app_instance.state.session_factory() as session:
        teacher = User(
            email="task-teacher@example.com",
            password_hash="unused",
            role=UserRole.TEACHER,
        )
        session.add(teacher)
        await session.flush()
        course = Course(
            owner_id=teacher.id,
            template=CourseTemplate.DATA_STRUCTURES,
            code="DS-TASK",
            name="Task Runner Course",
            description="",
            semester="2026-Fall",
        )
        session.add(course)
        await session.flush()
        course_index = CourseIndex(
            course_id=course.id,
            version=1,
            dense_status=IndexComponentStatus.READY,
            lexical_status=IndexComponentStatus.READY,
            lexical_path="provided-by-fake-factory",
            status=CourseIndexStatus.READY,
        )
        session.add(course_index)
        await session.flush()
        dataset = EvalDataset(
            course_id=course.id,
            name="Frozen task fixture",
            description="",
            type=EvalDatasetType.INTENT_ROUTING,
            version=1,
            status=EvalDatasetStatus.FROZEN,
            created_by=teacher.id,
            frozen_at=now,
        )
        session.add(dataset)
        await session.flush()
        session.add(
            EvalCase(
                dataset_id=dataset.id,
                case_key="diagnose",
                input=case_input,
                expected={"expected_intent": "DIAGNOSE"},
                labels={},
            )
        )
        await session.commit()
        run = await service.queue_run(
            session,
            dataset_id=dataset.id,
            teacher=teacher,
            launch_config=dict(LAUNCH_CONFIG),
        )
        return run.id


async def test_celery_task_executes_queued_eval_run(app_instance, tmp_path):
    app_instance.state.settings.evaluation_report_root = tmp_path / "eval-reports"
    run_id = await _seed_queued_run(
        app_instance, case_input={"query": "帮我诊断知识缺口"}
    )

    await run_eval_run_job(
        run_id,
        settings=app_instance.state.settings,
        session_factory=app_instance.state.session_factory,
    )

    async with app_instance.state.session_factory() as session:
        run = await session.get(EvalRun, run_id)
        assert run is not None
        assert run.status == EvalRunStatus.SUCCEEDED
        assert run.started_at is not None
        assert run.finished_at is not None
        assert run.git_commit != "server-detected-pending"
        assert run.metrics["dataset_type"] == "INTENT_ROUTING"
        assert run.metrics["metrics"]["routing"]["macro_f1"] == 1.0
        # The case runner persists its own measured child run.
        all_runs = (await session.scalars(select(EvalRun))).all()
        assert len(all_runs) == 2


async def test_celery_task_records_runner_failures(app_instance):
    run_id = await _seed_queued_run(app_instance, case_input={})

    await run_eval_run_job(
        run_id,
        settings=app_instance.state.settings,
        session_factory=app_instance.state.session_factory,
    )

    async with app_instance.state.session_factory() as session:
        run = await session.get(EvalRun, run_id)
        assert run is not None
        assert run.status == EvalRunStatus.FAILED
        assert run.error_code == "EVAL_CASE_QUERY_MISSING"
        assert run.error_message
        assert run.finished_at is not None
        assert run.metrics is None


async def test_celery_task_ignores_runs_that_are_not_queued(app_instance):
    run_id = await _seed_queued_run(
        app_instance, case_input={"query": "帮我诊断知识缺口"}
    )
    async with app_instance.state.session_factory() as session:
        run = await session.get(EvalRun, run_id)
        assert run is not None
        run.status = EvalRunStatus.RUNNING
        await session.commit()

    await run_eval_run_job(
        run_id,
        settings=app_instance.state.settings,
        session_factory=app_instance.state.session_factory,
    )

    async with app_instance.state.session_factory() as session:
        run = await session.get(EvalRun, run_id)
        assert run is not None
        assert run.status == EvalRunStatus.RUNNING
        assert run.metrics is None


async def test_api_dispatches_queued_run_through_injected_hook(client, app_instance):
    _, tokens = await register_and_login(
        client, "eval-dispatch-ok@example.com", role="TEACHER"
    )
    course = await create_course(client, tokens)
    dataset, _ = await _dataset_with_case(client, tokens, course["id"])

    dispatched: list[tuple[uuid.UUID, dict]] = []

    async def recording_dispatcher(run_id, launch):
        dispatched.append((run_id, launch))

    app_instance.state.evaluation_run_dispatcher = recording_dispatcher
    try:
        run = await _freeze_and_run(
            client, app_instance, tokens, course["id"], dataset["id"]
        )
    finally:
        app_instance.state.evaluation_run_dispatcher = None

    assert run["status"] == "QUEUED"
    assert dispatched and dispatched[0][0] == uuid.UUID(run["id"])
    assert dispatched[0][1]["index_version"] == 1


async def test_api_marks_run_failed_when_dispatch_fails(client, app_instance):
    _, tokens = await register_and_login(
        client, "eval-dispatch-fail@example.com", role="TEACHER"
    )
    course = await create_course(client, tokens)
    dataset, _ = await _dataset_with_case(client, tokens, course["id"])

    def broken_dispatcher(run_id, launch):
        raise RuntimeError("broker unavailable")

    app_instance.state.evaluation_run_dispatcher = broken_dispatcher
    try:
        run = await _freeze_and_run(
            client, app_instance, tokens, course["id"], dataset["id"]
        )
    finally:
        app_instance.state.evaluation_run_dispatcher = None

    # The enqueue failure must not break the API contract...
    assert run["status"] == "QUEUED"
    # ...but the run must not hang in QUEUED forever.
    async with app_instance.state.session_factory() as session:
        stored = await session.scalar(
            select(EvalRun).where(EvalRun.id == uuid.UUID(run["id"]))
        )
        assert stored is not None
        assert stored.status == EvalRunStatus.FAILED
        assert stored.error_code == "EVAL_DISPATCH_FAILED"
        assert stored.error_message
