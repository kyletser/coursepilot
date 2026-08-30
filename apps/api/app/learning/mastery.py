from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Final

from .errors import IdempotencyKeyMismatchError, QuizNotApprovedError
from .types import Difficulty, ReviewStatus, enum_token, is_approved

DIFFICULTY_WEIGHTS: Final[Mapping[Difficulty, float]] = MappingProxyType(
    {
        Difficulty.EASY: 0.75,
        Difficulty.MEDIUM: 1.0,
        Difficulty.HARD: 1.25,
    }
)


class IdempotencyOutcome(StrEnum):
    APPLIED = "APPLIED"
    REPLAYED = "REPLAYED"


@dataclass(frozen=True, slots=True)
class MasteryState:
    alpha: float = 1.0
    beta: float = 1.0
    attempt_count: int = 0

    def __post_init__(self) -> None:
        if not isfinite(self.alpha) or self.alpha <= 0:
            raise ValueError("alpha must be a finite positive number")
        if not isfinite(self.beta) or self.beta <= 0:
            raise ValueError("beta must be a finite positive number")
        if (
            isinstance(self.attempt_count, bool)
            or not isinstance(self.attempt_count, int)
            or self.attempt_count < 0
        ):
            raise ValueError("attempt_count must be a non-negative integer")

    @property
    def mastery(self) -> float:
        return self.alpha / (self.alpha + self.beta)


@dataclass(frozen=True, slots=True)
class MasteryUpdateResult:
    """Result persisted for an idempotency key and replayed on duplicates."""

    idempotency_key: str
    outcome: IdempotencyOutcome
    before: MasteryState
    after: MasteryState
    correct: bool
    difficulty: Difficulty
    weight: float

    def __post_init__(self) -> None:
        if not self.idempotency_key.strip():
            raise ValueError("idempotency_key must not be blank")

    @property
    def applied(self) -> bool:
        return self.outcome is IdempotencyOutcome.APPLIED

    @property
    def replayed(self) -> bool:
        return self.outcome is IdempotencyOutcome.REPLAYED

    @property
    def mastery(self) -> float:
        return self.after.mastery

    def as_replayed(self) -> MasteryUpdateResult:
        return replace(self, outcome=IdempotencyOutcome.REPLAYED)


def difficulty_weight(difficulty: Difficulty | str) -> float:
    try:
        normalized = Difficulty(enum_token(difficulty))
    except ValueError as exc:
        raise ValueError(f"Unsupported quiz difficulty: {difficulty!r}") from exc
    return DIFFICULTY_WEIGHTS[normalized]


def quiz_is_teacher_approved(status: ReviewStatus | str | object) -> bool:
    """Only an explicit APPROVED review status opens the mastery gate."""

    return is_approved(status)


def require_teacher_approved_quiz(
    status: ReviewStatus | str | object,
) -> None:
    if quiz_is_teacher_approved(status):
        return
    normalized = enum_token(status)
    raise QuizNotApprovedError(
        "Only teacher-approved quiz items can update mastery",
        details={"quiz_status": normalized},
    )


def beta_bernoulli_update(
    state: MasteryState,
    correct: bool,
    difficulty: Difficulty | str,
) -> MasteryState:
    """Apply the fixed difficulty weight without doing any I/O."""

    if not isinstance(correct, bool):
        raise TypeError("correct must be a bool")
    weight = difficulty_weight(difficulty)
    return MasteryState(
        alpha=state.alpha + (weight if correct else 0.0),
        beta=state.beta + (0.0 if correct else weight),
        attempt_count=state.attempt_count + 1,
    )


def update_mastery(
    state: MasteryState,
    correct: bool,
    difficulty: Difficulty | str,
    quiz_status: ReviewStatus | str | object,
) -> MasteryState:
    """Guarded mastery update used by application services."""

    require_teacher_approved_quiz(quiz_status)
    return beta_bernoulli_update(state, correct, difficulty)


def process_mastery_attempt(
    state: MasteryState,
    *,
    idempotency_key: str,
    correct: bool,
    difficulty: Difficulty | str,
    quiz_status: ReviewStatus | str | object,
    existing_result: MasteryUpdateResult | None = None,
) -> MasteryUpdateResult:
    """Create a result or replay the stored result without applying twice.

    The caller remains responsible for loading ``existing_result`` and persisting
    the attempt plus mastery state in one transaction.  This function makes the
    deterministic replay contract explicit and never mutates either state.
    """

    if not idempotency_key.strip():
        raise ValueError("idempotency_key must not be blank")
    if existing_result is not None:
        if existing_result.idempotency_key != idempotency_key:
            raise IdempotencyKeyMismatchError(
                "Stored result belongs to a different idempotency key",
                details={
                    "requested_key": idempotency_key,
                    "stored_key": existing_result.idempotency_key,
                },
            )
        return existing_result.as_replayed()

    normalized_difficulty = Difficulty(enum_token(difficulty))
    after = update_mastery(
        state,
        correct=correct,
        difficulty=normalized_difficulty,
        quiz_status=quiz_status,
    )
    return MasteryUpdateResult(
        idempotency_key=idempotency_key,
        outcome=IdempotencyOutcome.APPLIED,
        before=state,
        after=after,
        correct=correct,
        difficulty=normalized_difficulty,
        weight=DIFFICULTY_WEIGHTS[normalized_difficulty],
    )


# Readable aliases for service code and callers using the product terminology.
mastery_update_allowed = quiz_is_teacher_approved
apply_mastery_attempt = process_mastery_attempt
