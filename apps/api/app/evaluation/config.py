from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.evaluation._json import JsonValue, freeze_json_object, thaw_json


def _non_blank(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """The complete, deeply immutable provenance for one evaluation run."""

    git_commit: str
    dataset_version: str
    index_version: str
    model_versions: Mapping[str, str]
    prompt_version: str
    retrieval_parameters: Mapping[str, JsonValue]
    hardware: Mapping[str, JsonValue]

    def __post_init__(self) -> None:
        for field_name in (
            "git_commit",
            "dataset_version",
            "index_version",
            "prompt_version",
        ):
            object.__setattr__(
                self,
                field_name,
                _non_blank(getattr(self, field_name), field_name=field_name),
            )

        if not isinstance(self.model_versions, Mapping) or not self.model_versions:
            raise ValueError("model_versions must record at least one model")
        models: dict[str, str] = {}
        for role, version in self.model_versions.items():
            normalized_role = _non_blank(role, field_name="model role")
            models[normalized_role] = _non_blank(
                version, field_name=f"model_versions.{normalized_role}"
            )
        frozen_models = freeze_json_object(models, field_name="model_versions")
        object.__setattr__(self, "model_versions", frozen_models)

        retrieval = freeze_json_object(
            self.retrieval_parameters, field_name="retrieval_parameters"
        )
        hardware = freeze_json_object(self.hardware, field_name="hardware")
        if not retrieval:
            raise ValueError("retrieval_parameters must not be empty")
        if not hardware:
            raise ValueError("hardware must not be empty")
        object.__setattr__(self, "retrieval_parameters", retrieval)
        object.__setattr__(self, "hardware", hardware)

    @property
    def retrieval_params(self) -> Mapping[str, JsonValue]:
        """A short compatibility alias without creating a second source of truth."""

        return self.retrieval_parameters

    def to_dict(self) -> dict[str, Any]:
        return {
            "git_commit": self.git_commit,
            "dataset_version": self.dataset_version,
            "index_version": self.index_version,
            "model_versions": thaw_json(self.model_versions),
            "prompt_version": self.prompt_version,
            "retrieval_parameters": thaw_json(self.retrieval_parameters),
            "hardware": thaw_json(self.hardware),
        }
