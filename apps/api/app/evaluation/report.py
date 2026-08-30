from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.evaluation._json import JsonValue, freeze_json_object, thaw_json
from app.evaluation.config import EvalConfig
from app.evaluation.dataset import FrozenEvalDataset
from app.evaluation.metrics import (
    AbstentionMetrics,
    CitationMetrics,
    PathMetrics,
    RetrievalMetrics,
    RoutingMetrics,
)

REPORT_SCHEMA_VERSION = "coursepilot.evaluation-report/1.0.0"


def _non_blank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value.strip()


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class DatasetReference:
    dataset_id: str
    dataset_type: str
    version: str
    frozen_at: datetime
    content_sha256: str
    case_count: int

    @classmethod
    def from_dataset(cls, dataset: FrozenEvalDataset) -> DatasetReference:
        return cls(
            dataset_id=dataset.dataset_id,
            dataset_type=dataset.dataset_type,
            version=dataset.version,
            frozen_at=dataset.frozen_at,
            content_sha256=dataset.content_sha256,
            case_count=len(dataset.cases),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_type": self.dataset_type,
            "version": self.version,
            "frozen_at": _iso_utc(self.frozen_at),
            "content_sha256": self.content_sha256,
            "case_count": self.case_count,
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    report_version: str
    generated_at: datetime
    dataset: DatasetReference
    config: EvalConfig
    metrics: Mapping[str, JsonValue]
    schema_version: str = REPORT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "report_version",
            _non_blank(self.report_version, field_name="report_version"),
        )
        object.__setattr__(
            self,
            "generated_at",
            _as_utc(self.generated_at, field_name="generated_at"),
        )
        object.__setattr__(
            self,
            "schema_version",
            _non_blank(self.schema_version, field_name="schema_version"),
        )
        frozen_metrics = freeze_json_object(self.metrics, field_name="metrics")
        if not frozen_metrics:
            raise ValueError(
                "a report must contain at least one measured metric family"
            )
        object.__setattr__(self, "metrics", frozen_metrics)
        if self.dataset.version != self.config.dataset_version:
            raise ValueError("config dataset_version does not match the frozen dataset")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "report_version": self.report_version,
            "generated_at": _iso_utc(self.generated_at),
            "dataset": self.dataset.to_dict(),
            "config": self.config.to_dict(),
            "metrics": thaw_json(self.metrics),
        }

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(
            self.to_dict(),
            allow_nan=False,
            ensure_ascii=False,
            indent=indent,
            sort_keys=True,
        )

    def write_json(self, path: str | Path, *, overwrite: bool = False) -> Path:
        target = Path(path)
        if target.suffix.casefold() != ".json":
            raise ValueError("evaluation report path must end with .json")
        mode = "w" if overwrite else "x"
        with target.open(mode, encoding="utf-8", newline="\n") as stream:
            stream.write(self.to_json())
            stream.write("\n")
        return target


def build_evaluation_report(
    *,
    report_version: str,
    generated_at: datetime,
    dataset: FrozenEvalDataset,
    config: EvalConfig,
    retrieval: RetrievalMetrics | None = None,
    citations: CitationMetrics | None = None,
    abstention: AbstentionMetrics | None = None,
    routing: RoutingMetrics | None = None,
    paths: PathMetrics | None = None,
) -> EvaluationReport:
    """Build a report exclusively from already-computed deterministic results."""

    measured: dict[str, Any] = {}
    if retrieval is not None:
        measured["retrieval"] = retrieval.to_dict()
    if citations is not None:
        measured["citations"] = citations.to_dict()
    if abstention is not None:
        measured["abstention"] = abstention.to_dict()
    if routing is not None:
        measured["routing"] = routing.to_dict()
    if paths is not None:
        measured["paths"] = paths.to_dict()
    if not measured:
        raise ValueError("at least one computed metric result is required")
    return EvaluationReport(
        report_version=report_version,
        generated_at=generated_at,
        dataset=DatasetReference.from_dataset(dataset),
        config=config,
        metrics=measured,
    )
