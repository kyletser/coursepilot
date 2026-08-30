from __future__ import annotations

from enum import StrEnum


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Difficulty(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


def enum_token(value: object) -> str:
    """Normalize strings and foreign Enum values at the domain boundary."""

    raw_value = getattr(value, "value", value)
    return str(raw_value).strip().upper()


def is_approved(value: object) -> bool:
    return enum_token(value) == ReviewStatus.APPROVED.value
