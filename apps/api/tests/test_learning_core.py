from __future__ import annotations

import pytest

from app.learning import (
    Concept,
    Difficulty,
    IdempotencyOutcome,
    MasteryState,
    PrerequisiteCycleError,
    PrerequisiteRelation,
    PrerequisiteSelfLoopError,
    QuizNotApprovedError,
    RelationType,
    ReviewStatus,
    TargetConceptNotApprovedError,
    build_learning_path,
    expand_prerequisite_ancestors,
    process_mastery_attempt,
    update_mastery,
    validate_prerequisite_edge,
    validate_prerequisite_graph,
)


@pytest.mark.parametrize(
    ("difficulty", "correct", "expected_alpha", "expected_beta"),
    [
        (Difficulty.EASY, True, 1.75, 1.0),
        (Difficulty.MEDIUM, False, 1.0, 2.0),
        (Difficulty.HARD, True, 2.25, 1.0),
    ],
)
def test_guarded_beta_bernoulli_update_uses_fixed_difficulty_weights(
    difficulty, correct, expected_alpha, expected_beta
):
    updated = update_mastery(MasteryState(), correct, difficulty, ReviewStatus.APPROVED)

    assert updated == MasteryState(expected_alpha, expected_beta, 1)

    with pytest.raises(QuizNotApprovedError) as exc_info:
        update_mastery(updated, True, Difficulty.HARD, ReviewStatus.PENDING)
    assert exc_info.value.code == "QUIZ_NOT_APPROVED"


def test_duplicate_attempt_replays_original_result_without_another_update():
    first = process_mastery_attempt(
        MasteryState(),
        idempotency_key="attempt-001",
        correct=True,
        difficulty=Difficulty.HARD,
        quiz_status=ReviewStatus.APPROVED,
    )
    replay = process_mastery_attempt(
        first.after,
        idempotency_key="attempt-001",
        correct=False,
        difficulty=Difficulty.EASY,
        quiz_status=ReviewStatus.REJECTED,
        existing_result=first,
    )

    assert first.outcome is IdempotencyOutcome.APPLIED
    assert replay.outcome is IdempotencyOutcome.REPLAYED
    assert replay.after == first.after
    assert replay.after.attempt_count == 1
    assert replay.correct is True


def test_prerequisite_validation_rejects_self_loops_and_longer_cycles():
    with pytest.raises(PrerequisiteSelfLoopError) as self_loop:
        validate_prerequisite_edge("sorting", "sorting")
    assert self_loop.value.code == "PREREQUISITE_SELF_LOOP"

    cycle = [
        PrerequisiteRelation("a", "b", ReviewStatus.APPROVED),
        PrerequisiteRelation("b", "c", ReviewStatus.APPROVED),
        PrerequisiteRelation("c", "a", ReviewStatus.APPROVED),
    ]
    with pytest.raises(PrerequisiteCycleError) as cycle_error:
        validate_prerequisite_graph(cycle)
    assert cycle_error.value.code == "PREREQUISITE_CYCLE_DETECTED"
    assert cycle_error.value.cycle == ("a", "b", "c", "a")


def test_ancestor_expansion_is_capped_at_four_and_uses_only_approved_edges():
    relations = [
        PrerequisiteRelation("level-1", "target", ReviewStatus.APPROVED),
        PrerequisiteRelation("level-2", "level-1", ReviewStatus.APPROVED),
        PrerequisiteRelation("level-3", "level-2", ReviewStatus.APPROVED),
        PrerequisiteRelation("level-4", "level-3", ReviewStatus.APPROVED),
        PrerequisiteRelation("level-5", "level-4", ReviewStatus.APPROVED),
        PrerequisiteRelation("pending", "target", ReviewStatus.PENDING),
        PrerequisiteRelation(
            "related",
            "target",
            ReviewStatus.APPROVED,
            RelationType.RELATED_TO,
        ),
    ]

    assert expand_prerequisite_ancestors("target", relations) == (
        "level-1",
        "level-2",
        "level-3",
        "level-4",
    )
    with pytest.raises(ValueError, match="between 0 and 4"):
        expand_prerequisite_ancestors("target", relations, max_depth=5)


def test_learning_path_is_stable_topological_and_prioritizes_weak_ready_nodes():
    concepts = [
        Concept("target", ReviewStatus.APPROVED),
        Concept("strong", ReviewStatus.APPROVED),
        Concept("weak-first", ReviewStatus.APPROVED),
        Concept("weak-second", ReviewStatus.APPROVED),
        Concept("unreviewed", ReviewStatus.PENDING),
    ]
    relations = [
        PrerequisiteRelation("strong", "target", ReviewStatus.APPROVED),
        PrerequisiteRelation("weak-second", "target", ReviewStatus.APPROVED),
        PrerequisiteRelation("weak-first", "target", ReviewStatus.APPROVED),
        PrerequisiteRelation("unreviewed", "target", ReviewStatus.APPROVED),
    ]
    mastery = {"strong": 0.9, "weak-first": 0.2, "weak-second": 0.4}

    assert build_learning_path("target", concepts, relations, mastery) == (
        "weak-first",
        "weak-second",
        "strong",
        "target",
    )

    concepts[0] = Concept("target", ReviewStatus.PENDING)
    with pytest.raises(TargetConceptNotApprovedError) as error:
        build_learning_path("target", concepts, relations, mastery)
    assert error.value.code == "LEARNING_PATH_TARGET_NOT_APPROVED"
