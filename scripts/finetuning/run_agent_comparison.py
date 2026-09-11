"""Compare two real chat endpoints through frozen PostgreSQL retrieval/Agent QA.

Uses existing frozen internal-v3 datasets, without rebuilding or relabeling them.
All database/model credentials are read from environment, never report arguments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from run_experiment import source_state
from sqlalchemy import select

from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.evaluation.runner import run_end_to_end_qa_evaluation
from app.models import (
    Course,
    CourseIndex,
    CourseIndexStatus,
    EvalDataset,
    EvalDatasetStatus,
    EvalDatasetType,
)


async def run(args):
    state = source_state()
    token = os.environ["COURSEPILOT_INFERENCE_TOKEN"]
    settings = get_settings()
    engine = create_engine(settings)
    sessions = create_session_factory(engine)
    args.output.mkdir(parents=True, exist_ok=False)
    completed = []
    try:
        async with sessions() as session:
            records = (
                await session.execute(
                    select(EvalDataset.id, Course.code, CourseIndex.version)
                    .join(Course, Course.id == EvalDataset.course_id)
                    .join(CourseIndex, CourseIndex.course_id == Course.id)
                    .where(
                        EvalDataset.name.like("内部正式-%-v3"),
                        EvalDataset.type == EvalDatasetType.END_TO_END_QA,
                        EvalDataset.status == EvalDatasetStatus.FROZEN,
                        CourseIndex.status == CourseIndexStatus.ACTIVE,
                        CourseIndex.deleted_at.is_(None),
                        EvalDataset.deleted_at.is_(None),
                        Course.code.in_(["CP-DEMO-DS", "CP-DEMO-OS"]),
                    )
                    .order_by(Course.code)
                )
            ).all()
        if len(records) != 2:
            raise ValueError(
                f"expected exactly two original QA datasets, got {len(records)}"
            )
        for position, (dataset_id, course, version) in enumerate(records):
            aliases = ["coursepilot-qwen3-base", "coursepilot-qwen3-sft"]
            if position % 2:
                aliases.reverse()  # Counterbalance which model is run first.
            for alias in aliases:
                configured = settings.model_copy(
                    update={
                        "llm_base_url": args.base_url,
                        "llm_api_key": token,
                        "llm_model": alias,
                    }
                )
                result = await run_end_to_end_qa_evaluation(
                    session_factory=sessions,
                    settings=configured,
                    dataset_id=dataset_id,
                    index_version=version,
                    output_dir=args.output,
                    git_commit=state["git_commit"],
                    git_dirty=False,
                    report_version=f"sft-paired-{course.lower()}-{alias}",
                    prompt_version="coursepilot-grounded-render-empty-claims-v2",
                    route_top_k=10,
                    fusion_top_k=8,
                    final_top_k=5,
                )
                if result.report["dataset"]["case_count"] != 40:
                    raise ValueError("unexpected frozen QA count")
                item = {
                    "course": course,
                    "model": alias,
                    "report_path": str(result.report_path),
                    "metrics": result.report["metrics"],
                }
                completed.append(item)
                print(json.dumps(item, ensure_ascii=False), flush=True)
        with (args.output / "comparison-index.json").open(
            "x", encoding="utf-8"
        ) as handle:
            json.dump(
                {
                    "source": state,
                    "reports": completed,
                    "limitations": [
                        "80 original internal demo QA; not independent external benchmark",
                        "lexical citation support proxy, not semantic entailment",
                        "retrieval uses host hardware; chat uses remote single GPU",
                    ],
                },
                handle,
                ensure_ascii=False,
                indent=2,
            )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:18080/v1")
    asyncio.run(run(parser.parse_args()))
