from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Mapping[str, JsonValue] | Sequence[JsonValue]


def freeze_json(value: Any, *, field_name: str = "value") -> JsonValue:
    """Validate JSON-compatible input and return a deeply immutable copy."""

    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} cannot contain NaN or infinity")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key in sorted(value):
            if not isinstance(key, str):
                raise TypeError(f"{field_name} object keys must be strings")
            frozen[key] = freeze_json(value[key], field_name=f"{field_name}.{key}")
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(
            freeze_json(item, field_name=f"{field_name}[{index}]")
            for index, item in enumerate(value)
        )
    raise TypeError(f"{field_name} must contain only JSON-compatible values")


def freeze_json_object(
    value: Mapping[str, Any], *, field_name: str
) -> Mapping[str, JsonValue]:
    frozen = freeze_json(value, field_name=field_name)
    if not isinstance(frozen, Mapping):  # pragma: no cover - narrowed by signature
        raise TypeError(f"{field_name} must be a JSON object")
    return frozen


def thaw_json(value: JsonValue) -> Any:
    """Return ordinary dict/list objects suitable for JSON serialization."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str):
        return [thaw_json(item) for item in value]
    return value


def canonical_json(value: JsonValue) -> str:
    return json.dumps(
        thaw_json(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
