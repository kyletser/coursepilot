from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

import pytest

_SEED_NAMESPACE = runpy.run_path(
    str(Path(__file__).resolve().parents[3] / "scripts" / "seed_demo.py")
)
SeedError = _SEED_NAMESPACE["SeedError"]
_concept_chunk_ids = _SEED_NAMESPACE["_concept_chunk_ids"]
_ensure_demo_dataset = _SEED_NAMESPACE["_ensure_demo_dataset"]


class _FakeClient:
    def __init__(self, responses: dict[tuple[str, str], Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def call(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> Any:
        self.calls.append((method, path, json_body))
        return self.responses[(method, path)]


def test_concept_chunk_ids_only_uses_current_document_version() -> None:
    path = "/api/v1/courses/course-1/graph/candidates?status=APPROVED"
    client = _FakeClient(
        {
            (
                "GET",
                path,
            ): {
                "concepts": [
                    {
                        "name": "页面置换",
                        "source_chunk_id": "old-chunk",
                        "source": {"document": {"version_id": "version-1"}},
                    },
                    {
                        "name": "页面置换",
                        "source_chunk_id": "current-chunk",
                        "source": {"document": {"version_id": "version-2"}},
                    },
                ]
            }
        }
    )

    mapping = _concept_chunk_ids(
        client,
        "course-1",
        document_version_id="version-2",
    )

    assert mapping == {"页面置换": "current-chunk"}


def test_ensure_demo_dataset_updates_stale_draft_case() -> None:
    dataset_id = "dataset-1"
    list_path = "/api/v1/courses/course-1/eval-datasets"
    cases_path = f"/api/v1/eval-datasets/{dataset_id}/cases"
    update_path = "/api/v1/eval-cases/case-1"
    client = _FakeClient(
        {
            (
                "GET",
                list_path,
            ): [
                {
                    "id": dataset_id,
                    "name": "演示检索集-OS",
                    "status": "DRAFT",
                }
            ],
            (
                "GET",
                cases_path,
            ): [
                {
                    "id": "case-1",
                    "case_key": "retrieval-页面置换",
                    "input": {"query": "旧查询"},
                    "expected": {"relevant_chunk_ids": ["old-chunk"]},
                    "labels": {"concept": "页面置换"},
                }
            ],
            ("PATCH", update_path): {"id": "case-1"},
        }
    )
    desired_case = {
        "case_key": "retrieval-页面置换",
        "input": {"query": "什么是页面置换？"},
        "expected": {"relevant_chunk_ids": ["current-chunk"]},
        "labels": {"concept": "页面置换"},
    }

    changed = _ensure_demo_dataset(
        client,
        "course-1",
        dataset_name="演示检索集-OS",
        dataset_type="RETRIEVAL",
        cases=[desired_case],
    )

    assert changed == 1
    assert client.calls[-1] == (
        "PATCH",
        update_path,
        {
            "input": desired_case["input"],
            "expected": desired_case["expected"],
            "labels": desired_case["labels"],
        },
    )


def test_ensure_demo_dataset_does_not_mutate_frozen_case() -> None:
    dataset_id = "dataset-1"
    list_path = "/api/v1/courses/course-1/eval-datasets"
    cases_path = f"/api/v1/eval-datasets/{dataset_id}/cases"
    client = _FakeClient(
        {
            (
                "GET",
                list_path,
            ): [
                {
                    "id": dataset_id,
                    "name": "演示检索集-OS",
                    "status": "FROZEN",
                }
            ],
            (
                "GET",
                cases_path,
            ): [
                {
                    "id": "case-1",
                    "case_key": "retrieval-页面置换",
                    "input": {"query": "旧查询"},
                    "expected": {"relevant_chunk_ids": ["old-chunk"]},
                    "labels": {},
                }
            ],
        }
    )

    with pytest.raises(SeedError, match="已冻结"):
        _ensure_demo_dataset(
            client,
            "course-1",
            dataset_name="演示检索集-OS",
            dataset_type="RETRIEVAL",
            cases=[
                {
                    "case_key": "retrieval-页面置换",
                    "input": {"query": "新查询"},
                    "expected": {"relevant_chunk_ids": ["current-chunk"]},
                    "labels": {},
                }
            ],
        )

    assert all(method != "PATCH" for method, _path, _body in client.calls)
