from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from app.evaluation._json import (
    JsonValue,
    canonical_json,
    freeze_json_object,
    thaw_json,
)

DATASET_SCHEMA_VERSION = "coursepilot.eval-dataset/1.0.0"


class DatasetStatus(StrEnum):
    DRAFT = "DRAFT"
    FROZEN = "FROZEN"


class DatasetFrozenError(RuntimeError):
    """Raised when code attempts to mutate a frozen evaluation dataset."""


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
class EvalCase:
    case_id: str
    payload: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "case_id", _non_blank(self.case_id, field_name="case_id")
        )
        object.__setattr__(
            self,
            "payload",
            freeze_json_object(
                self.payload, field_name=f"case[{self.case_id}].payload"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"case_id": self.case_id, "payload": thaw_json(self.payload)}


@dataclass(frozen=True, slots=True)
class FrozenEvalDataset:
    dataset_id: str
    dataset_type: str
    version: str
    cases: tuple[EvalCase, ...]
    metadata: Mapping[str, JsonValue]
    frozen_at: datetime
    content_sha256: str
    schema_version: str = DATASET_SCHEMA_VERSION

    def to_dict(self, *, include_cases: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema_version": self.schema_version,
            "dataset_id": self.dataset_id,
            "dataset_type": self.dataset_type,
            "version": self.version,
            "status": DatasetStatus.FROZEN.value,
            "frozen_at": _iso_utc(self.frozen_at),
            "content_sha256": self.content_sha256,
            "case_count": len(self.cases),
            "metadata": thaw_json(self.metadata),
        }
        if include_cases:
            result["cases"] = [case.to_dict() for case in self.cases]
        return result


class EvalDataset:
    """A draft dataset whose domain methods become immutable after freeze()."""

    __slots__ = (
        "_cases",
        "_dataset_id",
        "_dataset_type",
        "_frozen_snapshot",
        "_metadata",
        "_version",
    )

    def __init__(
        self,
        *,
        dataset_id: str,
        dataset_type: str,
        version: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self._dataset_id = _non_blank(dataset_id, field_name="dataset_id")
        self._dataset_type = _non_blank(dataset_type, field_name="dataset_type")
        self._version = _non_blank(version, field_name="version")
        self._metadata = freeze_json_object(
            metadata or {}, field_name="dataset.metadata"
        )
        self._cases: dict[str, EvalCase] = {}
        self._frozen_snapshot: FrozenEvalDataset | None = None

    @property
    def dataset_id(self) -> str:
        return self._dataset_id

    @property
    def dataset_type(self) -> str:
        return self._dataset_type

    @property
    def version(self) -> str:
        return self._version

    @property
    def status(self) -> DatasetStatus:
        return (
            DatasetStatus.FROZEN
            if self._frozen_snapshot is not None
            else DatasetStatus.DRAFT
        )

    @property
    def is_frozen(self) -> bool:
        return self._frozen_snapshot is not None

    @property
    def frozen_at(self) -> datetime | None:
        return self._frozen_snapshot.frozen_at if self._frozen_snapshot else None

    @property
    def content_sha256(self) -> str | None:
        return self._frozen_snapshot.content_sha256 if self._frozen_snapshot else None

    @property
    def cases(self) -> tuple[EvalCase, ...]:
        return tuple(self._cases.values())

    @property
    def metadata(self) -> Mapping[str, JsonValue]:
        return self._metadata

    def _require_draft(self) -> None:
        if self.is_frozen:
            raise DatasetFrozenError(
                f"dataset {self.dataset_id}@{self.version} is frozen and immutable"
            )

    def add_case(self, case_id: str, payload: Mapping[str, Any]) -> EvalCase:
        self._require_draft()
        case = EvalCase(case_id=case_id, payload=payload)
        if case.case_id in self._cases:
            raise ValueError(f"duplicate evaluation case id: {case.case_id}")
        self._cases[case.case_id] = case
        return case

    def replace_case(self, case_id: str, payload: Mapping[str, Any]) -> EvalCase:
        self._require_draft()
        normalized_id = _non_blank(case_id, field_name="case_id")
        if normalized_id not in self._cases:
            raise KeyError(normalized_id)
        case = EvalCase(case_id=normalized_id, payload=payload)
        self._cases[normalized_id] = case
        return case

    update_case = replace_case

    def remove_case(self, case_id: str) -> EvalCase:
        self._require_draft()
        return self._cases.pop(case_id)

    def replace_metadata(self, metadata: Mapping[str, Any]) -> None:
        self._require_draft()
        self._metadata = freeze_json_object(metadata, field_name="dataset.metadata")

    def freeze(self, *, frozen_at: datetime | None = None) -> FrozenEvalDataset:
        if self._frozen_snapshot is not None:
            return self._frozen_snapshot
        if not self._cases:
            raise ValueError("cannot freeze an empty evaluation dataset")
        timestamp = _as_utc(frozen_at or datetime.now(UTC), field_name="frozen_at")
        cases = tuple(self._cases.values())
        hash_payload = {
            "schema_version": DATASET_SCHEMA_VERSION,
            "dataset_id": self.dataset_id,
            "dataset_type": self.dataset_type,
            "version": self.version,
            "metadata": thaw_json(self.metadata),
            "cases": [case.to_dict() for case in cases],
        }
        digest = hashlib.sha256(
            canonical_json(hash_payload).encode("utf-8")
        ).hexdigest()
        self._frozen_snapshot = FrozenEvalDataset(
            dataset_id=self.dataset_id,
            dataset_type=self.dataset_type,
            version=self.version,
            cases=cases,
            metadata=self.metadata,
            frozen_at=timestamp,
            content_sha256=digest,
        )
        return self._frozen_snapshot
