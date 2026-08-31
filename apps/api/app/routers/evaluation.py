from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Query, Request
from pydantic import Field, field_validator, model_validator
from sqlalchemy import select

from app.dependencies import SessionDep, TeacherUser
from app.errors import success_response
from app.evaluation import service
from app.evaluation.runner import run_three_baselines
from app.models import (
    BadCaseSourceType,
    BadCaseStatus,
    EvalDatasetType,
    EvalRun,
    EvalRunStatus,
)
from app.schemas import RequestModel

router = APIRouter(prefix="/api/v1", tags=["evaluation"])


class EvalDatasetCreateRequest(RequestModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=10000)
    type: EvalDatasetType
    version: int | None = Field(default=None, ge=1)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class EvalCaseCreateRequest(RequestModel):
    case_key: str = Field(min_length=1, max_length=255)
    input: dict[str, Any]
    expected: dict[str, Any]
    labels: dict[str, Any] = Field(default_factory=dict)

    @field_validator("case_key")
    @classmethod
    def strip_case_key(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class EvalCaseUpdateRequest(RequestModel):
    case_key: str | None = Field(default=None, min_length=1, max_length=255)
    input: dict[str, Any] | None = None
    expected: dict[str, Any] | None = None
    labels: dict[str, Any] | None = None

    @field_validator("case_key")
    @classmethod
    def strip_optional_case_key(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self


class EvalRunCreateRequest(RequestModel):
    index_version: int = Field(ge=1)
    report_version: str | None = Field(default=None, min_length=1, max_length=255)
    prompt_version: str = Field(
        default="no-generation/retrieval-eval-v1", min_length=1, max_length=255
    )
    route_top_k: int = Field(default=20, ge=1, le=100)
    fusion_top_k: int = Field(default=20, ge=1, le=100)
    final_top_k: int = Field(default=10, ge=1, le=100)
    rrf_k: int = Field(default=60, ge=1, le=1000)
    reranker_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    graph_depth: int = Field(default=2, ge=0, le=4)
    kg_weight: float = Field(default=1.0, ge=0, le=10)


async def _execute_eval_run(app_state: Any, run_id: uuid.UUID) -> None:
    async with app_state.session_factory() as session:
        run = await session.scalar(
            select(EvalRun).where(EvalRun.id == run_id).with_for_update()
        )
        if run is None or run.status != EvalRunStatus.QUEUED:
            return
        run.status = EvalRunStatus.RUNNING
        run.started_at = datetime.now(UTC)
        await session.commit()
        dataset_id = run.dataset_id
        launch = dict(run.config.get("launch", {}))

    try:
        result = await run_three_baselines(
            session_factory=app_state.session_factory,
            settings=app_state.settings,
            dataset_id=dataset_id,
            index_version=int(launch["index_version"]),
            output_dir=Path("evaluation-reports"),
            report_version=launch.get("report_version"),
            prompt_version=str(launch["prompt_version"]),
            route_top_k=int(launch["route_top_k"]),
            fusion_top_k=int(launch["fusion_top_k"]),
            final_top_k=int(launch["final_top_k"]),
            rrf_k=int(launch["rrf_k"]),
            reranker_timeout_seconds=float(launch["reranker_timeout_seconds"]),
            graph_depth=int(launch["graph_depth"]),
            kg_weight=float(launch["kg_weight"]),
        )
    except Exception as exc:  # noqa: BLE001 - the job records every terminal failure
        async with app_state.session_factory() as session:
            run = await session.get(EvalRun, run_id)
            if run is not None:
                run.status = EvalRunStatus.FAILED
                run.error_code = getattr(exc, "code", "EVAL_RUN_FAILED")
                run.error_message = str(exc)[:4000]
                run.finished_at = datetime.now(UTC)
                await session.commit()
        return

    async with app_state.session_factory() as session:
        run = await session.get(EvalRun, run_id)
        if run is not None:
            run.status = EvalRunStatus.SUCCEEDED
            run.metrics = {
                "report_path": str(result.report_path),
                "baseline_run_ids": {
                    mode: str(child_id) for mode, child_id in result.eval_run_ids.items()
                },
                "baselines": result.report["baselines"],
            }
            provenance = result.report["provenance"]
            run.git_commit = str(provenance["git_commit"])
            run.finished_at = datetime.now(UTC)
            await session.commit()


class BadCaseCreateRequest(RequestModel):
    course_id: uuid.UUID
    source_type: BadCaseSourceType
    source_id: uuid.UUID
    category: str = Field(min_length=1, max_length=64)
    notes: str = Field(default="", max_length=10000)

    @field_validator("category")
    @classmethod
    def strip_category(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()


class BadCaseUpdateRequest(RequestModel):
    status: BadCaseStatus | None = None
    notes: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("at least one field must be provided")
        return self


@router.post("/courses/{course_id}/eval-datasets", status_code=201)
async def create_eval_dataset(
    course_id: uuid.UUID,
    payload: EvalDatasetCreateRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    dataset = await service.create_dataset(
        session,
        course_id=course_id,
        teacher=teacher,
        name=payload.name,
        description=payload.description,
        dataset_type=payload.type,
        version=payload.version,
    )
    return success_response(
        request,
        service.dataset_data(dataset, case_count=0),
        status_code=201,
    )


@router.get("/courses/{course_id}/eval-datasets")
async def list_eval_datasets(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    datasets = await service.list_datasets(
        session, course_id=course_id, teacher=teacher
    )
    data = []
    for dataset in datasets:
        count = await service.dataset_case_count(session, dataset.id)
        data.append(service.dataset_data(dataset, case_count=count))
    return success_response(request, data)


@router.get("/eval-datasets/{dataset_id}")
async def get_eval_dataset(
    dataset_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    dataset, count, digest = await service.get_dataset(
        session, dataset_id=dataset_id, teacher=teacher
    )
    return success_response(
        request,
        service.dataset_data(dataset, case_count=count, content_sha256=digest),
    )


@router.post("/eval-datasets/{dataset_id}/cases", status_code=201)
async def create_eval_case(
    dataset_id: uuid.UUID,
    payload: EvalCaseCreateRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    case = await service.add_case(
        session,
        dataset_id=dataset_id,
        teacher=teacher,
        case_key=payload.case_key,
        case_input=payload.input,
        expected=payload.expected,
        labels=payload.labels,
    )
    return success_response(request, service.case_data(case), status_code=201)


@router.get("/eval-datasets/{dataset_id}/cases")
async def list_eval_cases(
    dataset_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    cases = await service.list_cases(session, dataset_id=dataset_id, teacher=teacher)
    return success_response(request, [service.case_data(case) for case in cases])


@router.patch("/eval-cases/{case_id}")
async def update_eval_case(
    case_id: uuid.UUID,
    payload: EvalCaseUpdateRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    case = await service.update_case(
        session,
        case_id=case_id,
        teacher=teacher,
        changes=payload.model_dump(exclude_unset=True, exclude_none=True),
    )
    return success_response(request, service.case_data(case))


@router.post("/eval-datasets/{dataset_id}/freeze")
async def freeze_eval_dataset(
    dataset_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    dataset, count, digest = await service.freeze_dataset(
        session, dataset_id=dataset_id, teacher=teacher
    )
    return success_response(
        request,
        service.dataset_data(dataset, case_count=count, content_sha256=digest),
    )


@router.post("/eval-datasets/{dataset_id}/runs", status_code=202)
async def create_eval_run(
    dataset_id: uuid.UUID,
    payload: EvalRunCreateRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    teacher: TeacherUser,
):
    launch = payload.model_dump()
    run = await service.queue_run(
        session,
        dataset_id=dataset_id,
        teacher=teacher,
        launch_config=launch,
    )
    dispatcher = getattr(request.app.state, "evaluation_run_dispatcher", None)
    if dispatcher is not None:
        await dispatcher(run.id, launch)
    elif request.app.state.settings.environment != "test":
        background_tasks.add_task(_execute_eval_run, request.app.state, run.id)
    return success_response(request, service.run_data(run), status_code=202)


@router.get("/eval-runs/{run_id}")
async def get_eval_run(
    run_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    run = await service.get_run(session, run_id=run_id, teacher=teacher)
    return success_response(request, service.run_data(run))


@router.get("/courses/{course_id}/eval-runs")
async def list_eval_runs(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
    dataset_id: uuid.UUID | None = None,
    status: EvalRunStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    rows, total = await service.list_eval_runs(
        session,
        course_id=course_id,
        teacher=teacher,
        dataset_id=dataset_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return success_response(
        request,
        {
            "items": [service.run_list_data(run, dataset) for run, dataset in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


@router.post("/bad-cases", status_code=201)
async def create_bad_case(
    payload: BadCaseCreateRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    bad_case = await service.create_bad_case(
        session,
        course_id=payload.course_id,
        teacher=teacher,
        source_type=payload.source_type,
        source_id=payload.source_id,
        category=payload.category,
        notes=payload.notes,
    )
    return success_response(request, service.bad_case_data(bad_case), status_code=201)


@router.get("/courses/{course_id}/bad-cases")
async def list_bad_cases(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
    status: BadCaseStatus | None = None,
    source_type: BadCaseSourceType | None = None,
    category: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    items, total = await service.list_bad_cases(
        session,
        course_id=course_id,
        teacher=teacher,
        status=status,
        source_type=source_type,
        category=category,
        limit=limit,
        offset=offset,
    )
    return success_response(
        request,
        {
            "items": [service.bad_case_data(item) for item in items],
            "total": total,
            "limit": limit,
            "offset": offset,
        },
    )


@router.get("/courses/{course_id}/students/learning-summary")
async def get_learning_summary(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    data = await service.learning_summary(
        session,
        course_id=course_id,
        teacher=teacher,
    )
    return success_response(request, data)


@router.patch("/bad-cases/{bad_case_id}")
async def update_bad_case(
    bad_case_id: uuid.UUID,
    payload: BadCaseUpdateRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    bad_case = await service.update_bad_case(
        session,
        bad_case_id=bad_case_id,
        teacher=teacher,
        status=payload.status,
        notes=payload.notes,
    )
    return success_response(request, service.bad_case_data(bad_case))
