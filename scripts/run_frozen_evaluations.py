#!/usr/bin/env python3
"""Run every frozen CoursePilot internal acceptance suite reproducibly."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.evaluation.runner import (
    run_end_to_end_qa_evaluation,
    run_intent_routing_evaluation,
    run_learning_path_evaluation,
    run_three_baselines,
)
from app.models import (
    Course,
    CourseIndex,
    CourseIndexStatus,
    EvalDataset,
    EvalDatasetStatus,
    EvalDatasetType,
)

EXPECTED_COUNTS = {
    EvalDatasetType.RETRIEVAL: 100,
    EvalDatasetType.END_TO_END_QA: 40,
    EvalDatasetType.INTENT_ROUTING: 25,
    EvalDatasetType.LEARNING_PATH: 15,
}


def _require_clean_worktree() -> str:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty:
        raise RuntimeError(
            "Formal evaluation requires a clean worktree so reports can state "
            "git_dirty=false. Commit or move local artifacts first."
        )
    return commit


async def run(args: argparse.Namespace) -> None:
    commit = _require_clean_worktree()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(EvalDataset, Course, CourseIndex)
                    .join(Course, Course.id == EvalDataset.course_id)
                    .join(
                        CourseIndex,
                        CourseIndex.course_id == Course.id,
                    )
                    .where(
                        EvalDataset.name.like("内部正式-%-v1"),
                        EvalDataset.status == EvalDatasetStatus.FROZEN,
                        CourseIndex.status == CourseIndexStatus.ACTIVE,
                        EvalDataset.deleted_at.is_(None),
                        CourseIndex.deleted_at.is_(None),
                    )
                    .order_by(Course.code, EvalDataset.type)
                )
            ).all()
        selected = [
            (dataset, course, course_index)
            for dataset, course, course_index in rows
            if course.code in {"CP-DEMO-DS", "CP-DEMO-OS"}
            and dataset.type in EXPECTED_COUNTS
        ]
        if len(selected) != 8:
            raise RuntimeError(
                f"Expected 8 frozen internal datasets, found {len(selected)}"
            )

        results: list[dict[str, object]] = []
        for dataset, course, course_index in selected:
            expected_count = EXPECTED_COUNTS[dataset.type]
            report_version = (
                f"internal-v1-{course.code.lower()}-{dataset.type.value.lower()}"
            )
            common = {
                "session_factory": session_factory,
                "settings": settings,
                "dataset_id": dataset.id,
                "index_version": course_index.version,
                "output_dir": output_dir,
                "git_commit": commit,
                "git_dirty": False,
                "report_version": report_version,
            }
            print(
                f"Running {course.code} {dataset.type.value} ({expected_count} cases)..."
            )
            if dataset.type == EvalDatasetType.RETRIEVAL:
                result = await run_three_baselines(**common)
            elif dataset.type == EvalDatasetType.END_TO_END_QA:
                result = await run_end_to_end_qa_evaluation(**common)
            elif dataset.type == EvalDatasetType.INTENT_ROUTING:
                result = await run_intent_routing_evaluation(**common)
            elif dataset.type == EvalDatasetType.LEARNING_PATH:
                result = await run_learning_path_evaluation(**common)
            else:  # pragma: no cover - guarded by selected filtering
                continue
            report = result.report
            if report["dataset"]["case_count"] != expected_count:
                raise RuntimeError(
                    f"{report_version} has {report['dataset']['case_count']} cases; "
                    f"expected {expected_count}"
                )
            results.append(
                {
                    "course": course.code,
                    "dataset_type": dataset.type.value,
                    "report_path": str(result.report_path),
                    "metrics": report["metrics"],
                }
            )
            print(json.dumps(results[-1], ensure_ascii=False, sort_keys=True))
        print(
            json.dumps({"git_commit": commit, "reports": results}, ensure_ascii=False)
        )
    finally:
        await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory outside the repository, keeping git_dirty=false for every run.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
