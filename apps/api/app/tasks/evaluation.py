from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.evaluation.runner import run_case_evaluation, run_three_baselines
from app.models import EvalDataset, EvalDatasetType, EvalRun, EvalRunStatus
from app.worker import celery_app


async def execute_eval_run(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    run_id: uuid.UUID,
) -> None:
    """Drive one QUEUED evaluation run to a terminal SUCCEEDED/FAILED state."""

    async with session_factory() as session:
        run = await session.scalar(
            select(EvalRun).where(EvalRun.id == run_id).with_for_update()
        )
        if run is None or run.status != EvalRunStatus.QUEUED:
            return
        run.status = EvalRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        await session.commit()
        dataset_id = run.dataset_id
        raw_launch = run.config.get("launch", {})
        if not isinstance(raw_launch, dict):
            raise TypeError("Evaluation launch config must be a mapping")
        launch = dict(raw_launch)
        dataset = await session.get(EvalDataset, dataset_id)
        dataset_type = dataset.type if dataset is not None else None

    raw_prompt_version = launch.get("prompt_version")
    prompt_version = str(raw_prompt_version) if raw_prompt_version else None
    try:
        if dataset_type is None:
            raise LookupError(
                "The evaluation dataset was removed before the run started"
            )
        if dataset_type == EvalDatasetType.RETRIEVAL:
            baseline_result = await run_three_baselines(
                session_factory=session_factory,
                settings=settings,
                dataset_id=dataset_id,
                index_version=int(launch["index_version"]),
                output_dir=settings.evaluation_report_root,
                report_version=launch.get("report_version"),
                prompt_version=prompt_version or "no-generation/retrieval-eval-v1",
                route_top_k=int(launch["route_top_k"]),
                fusion_top_k=int(launch["fusion_top_k"]),
                final_top_k=int(launch["final_top_k"]),
                rrf_k=int(launch["rrf_k"]),
                reranker_timeout_seconds=float(launch["reranker_timeout_seconds"]),
                graph_depth=int(launch["graph_depth"]),
                kg_weight=float(launch["kg_weight"]),
            )
            metrics_payload: dict[str, Any] = {
                "report_path": str(baseline_result.report_path),
                "baseline_run_ids": {
                    mode: str(child_id)
                    for mode, child_id in baseline_result.eval_run_ids.items()
                },
                "baselines": baseline_result.report["baselines"],
            }
            provenance = baseline_result.report["provenance"]
        else:
            case_result = await run_case_evaluation(
                session_factory=session_factory,
                settings=settings,
                dataset_id=dataset_id,
                dataset_type=dataset_type,
                index_version=int(launch["index_version"]),
                output_dir=settings.evaluation_report_root,
                report_version=launch.get("report_version"),
                prompt_version=prompt_version,
                route_top_k=int(launch["route_top_k"]),
                fusion_top_k=int(launch["fusion_top_k"]),
                final_top_k=int(launch["final_top_k"]),
                rrf_k=int(launch["rrf_k"]),
                reranker_timeout_seconds=float(launch["reranker_timeout_seconds"]),
            )
            metrics_payload = {
                "report_path": str(case_result.report_path),
                "eval_run_id": str(case_result.eval_run_id),
                "dataset_type": dataset_type.value,
                "metrics": case_result.report["metrics"],
            }
            provenance = case_result.report["provenance"]
    except Exception as exc:  # noqa: BLE001 - the job records every terminal failure
        async with session_factory() as session:
            run = await session.get(EvalRun, run_id)
            if run is not None:
                run.status = EvalRunStatus.FAILED
                run.error_code = getattr(exc, "code", "EVAL_RUN_FAILED")
                run.error_message = str(exc)[:4000]
                run.finished_at = datetime.now(UTC)
                await session.commit()
        return

    async with session_factory() as session:
        run = await session.get(EvalRun, run_id)
        if run is not None:
            run.status = EvalRunStatus.SUCCEEDED
            run.metrics = metrics_payload
            run.git_commit = str(provenance["git_commit"])
            run.finished_at = datetime.now(UTC)
            await session.commit()


async def run_eval_run_job(
    run_id: uuid.UUID | str,
    *,
    settings: Settings | None = None,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Async worker entry with injectable persistence dependencies."""

    resolved_settings = settings or get_settings()
    engine = None
    if session_factory is None:
        engine = create_engine(resolved_settings)
        session_factory = create_session_factory(engine)
    try:
        await execute_eval_run(
            session_factory, resolved_settings, uuid.UUID(str(run_id))
        )
    finally:
        if engine is not None:
            await engine.dispose()


def run_eval_run_job_sync(run_id: uuid.UUID | str) -> None:
    """Celery's prefork workers are synchronous; own one event loop per task."""

    asyncio.run(run_eval_run_job(run_id))


@celery_app.task(
    name="coursepilot.evaluation.run",
    acks_late=True,
    reject_on_worker_lost=True,
)
def process_eval_run(run_id: str) -> None:
    run_eval_run_job_sync(run_id)


def enqueue_eval_run(run_id: uuid.UUID | str) -> None:
    process_eval_run.apply_async(args=[str(run_id)], retry=False)
