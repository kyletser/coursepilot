from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import select

from app.models import CourseIndex, CourseIndexStatus, EvalRun, IndexComponentStatus
from app.routers import evaluation
from tests.helpers import (
    auth_headers,
    create_course,
    register_and_login,
)


@pytest_asyncio.fixture(autouse=True)
async def ensure_evaluation_routes(app_instance):
    if not any(
        getattr(route, "path", None) == "/api/v1/courses/{course_id}/eval-datasets"
        for route in app_instance.routes
    ):
        app_instance.include_router(evaluation.router)


async def _dataset_with_case(client, tokens, course_id: str) -> tuple[dict, dict]:
    created = await client.post(
        f"/api/v1/courses/{course_id}/eval-datasets",
        headers=auth_headers(tokens),
        json={
            "name": "Retrieval baseline",
            "type": "RETRIEVAL",
            "version": 1,
        },
    )
    assert created.status_code == 201, created.text
    dataset = created.json()["data"]
    added = await client.post(
        f"/api/v1/eval-datasets/{dataset['id']}/cases",
        headers=auth_headers(tokens),
        json={
            "case_key": "stack-definition",
            "input": {"query": "什么是栈？"},
            "expected": {"relevant_chunk_ids": ["chunk-1", "chunk-2"]},
            "labels": {"question_type": "single_concept"},
        },
    )
    assert added.status_code == 201, added.text
    return dataset, added.json()["data"]


async def _freeze_and_run(
    client, app_instance, tokens, course_id: str, dataset_id: str
) -> dict:
    frozen = await client.post(
        f"/api/v1/eval-datasets/{dataset_id}/freeze",
        headers=auth_headers(tokens),
    )
    assert frozen.status_code == 200, frozen.text
    async with app_instance.state.session_factory() as session:
        session.add(
            CourseIndex(
                course_id=uuid.UUID(course_id),
                version=1,
                dense_status=IndexComponentStatus.READY,
                lexical_status=IndexComponentStatus.READY,
                status=CourseIndexStatus.ACTIVE,
            )
        )
        await session.commit()
    run = await client.post(
        f"/api/v1/eval-datasets/{dataset_id}/runs",
        headers=auth_headers(tokens),
        json={
            "config": {
                "git_commit": "0123456789abcdef",
                "index_version": 1,
                "model_versions": {
                    "embedding": "BAAI/bge-m3",
                    "reranker": "BAAI/bge-reranker-v2-m3",
                },
                "prompt_version": "retrieval-v1",
                "retrieval_parameters": {"top_k": 10},
                "hardware": {"cpu": "test"},
                "experiment": "hybrid_rerank",
            },
            "case_results": {
                "stack-definition": {
                    "ranked_chunk_ids": ["chunk-1", "noise", "chunk-2"]
                }
            },
        },
    )
    assert run.status_code == 201, run.text
    return run.json()["data"]


async def test_dataset_owner_can_build_and_freeze_but_not_mutate(client):
    _, owner_tokens = await register_and_login(
        client, "eval-owner@example.com", role="TEACHER"
    )
    _, other_tokens = await register_and_login(
        client, "eval-other@example.com", role="TEACHER"
    )
    _, student_tokens = await register_and_login(
        client, "eval-student@example.com", role="STUDENT"
    )
    course = await create_course(client, owner_tokens)
    dataset, case = await _dataset_with_case(client, owner_tokens, course["id"])

    listed = await client.get(
        f"/api/v1/eval-datasets/{dataset['id']}/cases",
        headers=auth_headers(owner_tokens),
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["data"]] == [case["id"]]

    frozen = await client.post(
        f"/api/v1/eval-datasets/{dataset['id']}/freeze",
        headers=auth_headers(owner_tokens),
    )
    assert frozen.status_code == 200
    assert frozen.json()["data"]["status"] == "FROZEN"
    assert len(frozen.json()["data"]["content_sha256"]) == 64

    immutable = await client.patch(
        f"/api/v1/eval-cases/{case['id']}",
        headers=auth_headers(owner_tokens),
        json={"labels": {"changed": True}},
    )
    assert immutable.status_code == 409
    assert immutable.json()["error"]["code"] == "EVAL_DATASET_FROZEN"

    denied = await client.get(
        f"/api/v1/eval-datasets/{dataset['id']}/cases",
        headers=auth_headers(other_tokens),
    )
    assert denied.status_code == 403
    student_denied = await client.post(
        f"/api/v1/courses/{course['id']}/eval-datasets",
        headers=auth_headers(student_tokens),
        json={"name": "no", "type": "RETRIEVAL"},
    )
    assert student_denied.status_code == 403


async def test_run_persists_versioned_config_and_measured_metrics(client, app_instance):
    _, tokens = await register_and_login(
        client, "run-owner@example.com", role="TEACHER"
    )
    course = await create_course(client, tokens)
    dataset, _ = await _dataset_with_case(client, tokens, course["id"])
    run = await _freeze_and_run(
        client, app_instance, tokens, course["id"], dataset["id"]
    )

    assert run["status"] == "SUCCEEDED"
    assert run["metrics"]["schema_version"] == "coursepilot.eval-metrics/1.0.0"
    assert run["metrics"]["retrieval"]["recall_at_5"] == 1.0
    assert run["metrics"]["retrieval"]["mrr_at_5"] == 1.0
    assert run["config"]["dataset_version"] == 1
    assert run["config"]["experiment"] == "hybrid_rerank"
    assert len(run["config"]["dataset"]["content_sha256"]) == 64

    fetched = await client.get(
        f"/api/v1/eval-runs/{run['id']}", headers=auth_headers(tokens)
    )
    assert fetched.status_code == 200
    assert fetched.json()["data"]["metrics"] == run["metrics"]
    async with app_instance.state.session_factory() as session:
        stored = await session.scalar(
            select(EvalRun).where(EvalRun.id == uuid.UUID(run["id"]))
        )
        assert stored is not None
        assert stored.config == run["config"]
        assert stored.metrics == run["metrics"]


async def test_bad_case_source_and_updates_are_course_owner_scoped(
    client, app_instance
):
    _, owner_tokens = await register_and_login(
        client, "bad-owner@example.com", role="TEACHER"
    )
    _, other_tokens = await register_and_login(
        client, "bad-other@example.com", role="TEACHER"
    )
    course = await create_course(client, owner_tokens)
    dataset, _ = await _dataset_with_case(client, owner_tokens, course["id"])
    run = await _freeze_and_run(
        client,
        app_instance,
        owner_tokens,
        course["id"],
        dataset["id"],
    )

    created = await client.post(
        "/api/v1/bad-cases",
        headers=auth_headers(owner_tokens),
        json={
            "course_id": course["id"],
            "source_type": "EVAL_RUN",
            "source_id": run["id"],
            "category": "RETRIEVAL_MISS",
            "notes": "Inspect ranking",
        },
    )
    assert created.status_code == 201, created.text
    bad_case = created.json()["data"]

    denied = await client.patch(
        f"/api/v1/bad-cases/{bad_case['id']}",
        headers=auth_headers(other_tokens),
        json={"status": "FIXED"},
    )
    assert denied.status_code == 403

    fixed = await client.patch(
        f"/api/v1/bad-cases/{bad_case['id']}",
        headers=auth_headers(owner_tokens),
        json={"status": "FIXED", "notes": "Regression passes"},
    )
    assert fixed.status_code == 200
    assert fixed.json()["data"]["status"] == "FIXED"
    assert fixed.json()["data"]["resolved_at"] is not None
