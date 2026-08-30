from __future__ import annotations

from typing import Any


class LearningCoreError(ValueError):
    """Deterministic business-rule failure exposed by the learning core."""

    code = "LEARNING_CORE_ERROR"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class QuizNotApprovedError(LearningCoreError):
    code = "QUIZ_NOT_APPROVED"


class IdempotencyKeyMismatchError(LearningCoreError):
    code = "IDEMPOTENCY_KEY_MISMATCH"


class PrerequisiteSelfLoopError(LearningCoreError):
    code = "PREREQUISITE_SELF_LOOP"


class PrerequisiteCycleError(LearningCoreError):
    code = "PREREQUISITE_CYCLE_DETECTED"

    def __init__(self, cycle: tuple[object, ...]) -> None:
        self.cycle = cycle
        rendered = " -> ".join(str(concept_id) for concept_id in cycle)
        super().__init__(
            f"Approved prerequisite graph contains a cycle: {rendered}",
            details={"cycle": [str(concept_id) for concept_id in cycle]},
        )


class TargetConceptNotFoundError(LearningCoreError):
    code = "LEARNING_PATH_TARGET_NOT_FOUND"


class TargetConceptNotApprovedError(LearningCoreError):
    code = "LEARNING_PATH_TARGET_NOT_APPROVED"
