from __future__ import annotations

from collections import deque
from collections.abc import Hashable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from .errors import (
    PrerequisiteCycleError,
    PrerequisiteSelfLoopError,
    TargetConceptNotApprovedError,
    TargetConceptNotFoundError,
)
from .mastery import MasteryState
from .types import ReviewStatus, enum_token, is_approved

type ConceptId = Hashable
MAX_PREREQUISITE_DEPTH = 4
WEAK_MASTERY_THRESHOLD = 0.60


class RelationType(StrEnum):
    PREREQUISITE_OF = "PREREQUISITE_OF"
    RELATED_TO = "RELATED_TO"
    PART_OF = "PART_OF"
    CONTRASTS_WITH = "CONTRASTS_WITH"


@dataclass(frozen=True, slots=True)
class Concept:
    concept_id: ConceptId
    status: ReviewStatus | str = ReviewStatus.PENDING
    name: str | None = None


@dataclass(frozen=True, slots=True)
class PrerequisiteRelation:
    """Directed edge from ``prerequisite_id`` to ``concept_id``."""

    prerequisite_id: ConceptId
    concept_id: ConceptId
    status: ReviewStatus | str = ReviewStatus.PENDING
    relation_type: RelationType | str = RelationType.PREREQUISITE_OF

    @property
    def source_id(self) -> ConceptId:
        return self.prerequisite_id

    @property
    def target_id(self) -> ConceptId:
        return self.concept_id


@dataclass(frozen=True, slots=True)
class LearningPathStep:
    concept_id: ConceptId
    mastery: float
    is_weak: bool
    depth: int
    reason: str


@dataclass(frozen=True, slots=True)
class LearningPath:
    target_concept_id: ConceptId
    steps: tuple[LearningPathStep, ...]
    max_depth: int

    @property
    def concept_ids(self) -> tuple[ConceptId, ...]:
        return tuple(step.concept_id for step in self.steps)


def _is_prerequisite(relation: PrerequisiteRelation) -> bool:
    return enum_token(relation.relation_type) == RelationType.PREREQUISITE_OF.value


def approved_prerequisite_relations(
    relations: Iterable[PrerequisiteRelation],
) -> tuple[PrerequisiteRelation, ...]:
    """Return only teacher-approved prerequisite edges, preserving input order."""

    return tuple(
        relation
        for relation in relations
        if _is_prerequisite(relation) and is_approved(relation.status)
    )


def _prerequisite_relations(
    relations: Iterable[PrerequisiteRelation],
    *,
    approved_only: bool,
) -> tuple[PrerequisiteRelation, ...]:
    return tuple(
        relation
        for relation in relations
        if _is_prerequisite(relation)
        and (not approved_only or is_approved(relation.status))
    )


def detect_prerequisite_cycle(
    relations: Iterable[PrerequisiteRelation],
    *,
    approved_only: bool = True,
) -> tuple[ConceptId, ...] | None:
    """Return one deterministic cycle (with repeated start) or ``None``."""

    graph: dict[ConceptId, list[ConceptId]] = {}
    node_order: dict[ConceptId, None] = {}
    for relation in _prerequisite_relations(relations, approved_only=approved_only):
        node_order.setdefault(relation.prerequisite_id, None)
        node_order.setdefault(relation.concept_id, None)
        neighbours = graph.setdefault(relation.prerequisite_id, [])
        if relation.concept_id not in neighbours:
            neighbours.append(relation.concept_id)
        graph.setdefault(relation.concept_id, [])

    visiting: set[ConceptId] = set()
    visited: set[ConceptId] = set()
    stack: list[ConceptId] = []
    stack_index: dict[ConceptId, int] = {}

    def visit(node: ConceptId) -> tuple[ConceptId, ...] | None:
        visiting.add(node)
        stack_index[node] = len(stack)
        stack.append(node)
        for neighbour in graph[node]:
            if neighbour in visiting:
                start = stack_index[neighbour]
                return (*stack[start:], neighbour)
            if neighbour not in visited:
                cycle = visit(neighbour)
                if cycle is not None:
                    return cycle
        stack.pop()
        stack_index.pop(node)
        visiting.remove(node)
        visited.add(node)
        return None

    for node in node_order:
        if node not in visited:
            cycle = visit(node)
            if cycle is not None:
                return cycle
    return None


def validate_prerequisite_graph(
    relations: Iterable[PrerequisiteRelation],
    *,
    approved_only: bool = True,
) -> None:
    """Reject self-loops first, then any longer prerequisite cycle."""

    relevant = _prerequisite_relations(relations, approved_only=approved_only)
    for relation in relevant:
        if relation.prerequisite_id == relation.concept_id:
            concept_id = relation.concept_id
            raise PrerequisiteSelfLoopError(
                f"Concept {concept_id!r} cannot be its own prerequisite",
                details={"concept_id": str(concept_id)},
            )
    cycle = detect_prerequisite_cycle(relevant, approved_only=False)
    if cycle is not None:
        raise PrerequisiteCycleError(cycle)


def validate_prerequisite_edge(
    prerequisite_id: ConceptId,
    concept_id: ConceptId,
    existing_relations: Iterable[PrerequisiteRelation] = (),
) -> None:
    """Validate a proposed edge as if it were approved for publication."""

    proposed = PrerequisiteRelation(
        prerequisite_id=prerequisite_id,
        concept_id=concept_id,
        status=ReviewStatus.APPROVED,
    )
    validate_prerequisite_graph(
        (*approved_prerequisite_relations(existing_relations), proposed),
        approved_only=False,
    )


def _validate_depth(max_depth: int) -> None:
    if isinstance(max_depth, bool) or not isinstance(max_depth, int):
        raise TypeError("max_depth must be an integer")
    if not 0 <= max_depth <= MAX_PREREQUISITE_DEPTH:
        raise ValueError(f"max_depth must be between 0 and {MAX_PREREQUISITE_DEPTH}")


def prerequisite_ancestor_depths(
    target_concept_id: ConceptId,
    relations: Iterable[PrerequisiteRelation],
    *,
    max_depth: int = MAX_PREREQUISITE_DEPTH,
) -> dict[ConceptId, int]:
    """Breadth-first expansion of approved ancestors, excluding the target."""

    _validate_depth(max_depth)
    approved = approved_prerequisite_relations(relations)
    validate_prerequisite_graph(approved, approved_only=False)

    reverse_graph: dict[ConceptId, list[ConceptId]] = {}
    for relation in approved:
        prerequisites = reverse_graph.setdefault(relation.concept_id, [])
        if relation.prerequisite_id not in prerequisites:
            prerequisites.append(relation.prerequisite_id)

    depths: dict[ConceptId, int] = {}
    queue: deque[tuple[ConceptId, int]] = deque([(target_concept_id, 0)])
    while queue:
        concept_id, depth = queue.popleft()
        if depth == max_depth:
            continue
        for prerequisite_id in reverse_graph.get(concept_id, ()):
            ancestor_depth = depth + 1
            old_depth = depths.get(prerequisite_id)
            if old_depth is not None and old_depth <= ancestor_depth:
                continue
            depths[prerequisite_id] = ancestor_depth
            queue.append((prerequisite_id, ancestor_depth))
    return depths


def expand_prerequisite_ancestors(
    target_concept_id: ConceptId,
    relations: Iterable[PrerequisiteRelation],
    *,
    max_depth: int = MAX_PREREQUISITE_DEPTH,
) -> tuple[ConceptId, ...]:
    return tuple(
        prerequisite_ancestor_depths(target_concept_id, relations, max_depth=max_depth)
    )


def _normalize_concepts(
    concepts: Iterable[Concept] | Mapping[ConceptId, Concept | ReviewStatus | str],
) -> tuple[dict[ConceptId, Concept], dict[ConceptId, int]]:
    normalized: dict[ConceptId, Concept] = {}
    order: dict[ConceptId, int] = {}
    items = concepts.items() if isinstance(concepts, Mapping) else None
    if items is not None:
        source = (
            value
            if isinstance(value, Concept)
            else Concept(concept_id=concept_id, status=value)
            for concept_id, value in items
        )
    else:
        source = iter(concepts)

    for index, concept in enumerate(source):
        if not isinstance(concept, Concept):
            raise TypeError("concepts must contain Concept values")
        if concept.concept_id in normalized:
            raise ValueError(f"Duplicate concept id: {concept.concept_id!r}")
        normalized[concept.concept_id] = concept
        order[concept.concept_id] = index
    return normalized, order


def _mastery_value(
    concept_id: ConceptId,
    mastery_by_concept: Mapping[ConceptId, float | MasteryState],
) -> float:
    raw_value = mastery_by_concept.get(concept_id, 0.5)
    value = (
        raw_value.mastery if isinstance(raw_value, MasteryState) else float(raw_value)
    )
    if not isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"Mastery for {concept_id!r} must be between 0 and 1")
    return value


def plan_learning_path(
    target_concept_id: ConceptId,
    concepts: Iterable[Concept] | Mapping[ConceptId, Concept | ReviewStatus | str],
    relations: Iterable[PrerequisiteRelation],
    mastery_by_concept: Mapping[ConceptId, float | MasteryState] | None = None,
    *,
    max_depth: int = MAX_PREREQUISITE_DEPTH,
    weak_threshold: float = WEAK_MASTERY_THRESHOLD,
) -> LearningPath:
    """Build a stable, weakness-prioritized topological learning path."""

    _validate_depth(max_depth)
    if not isfinite(weak_threshold) or not 0.0 <= weak_threshold <= 1.0:
        raise ValueError("weak_threshold must be between 0 and 1")
    concept_by_id, concept_order = _normalize_concepts(concepts)
    target = concept_by_id.get(target_concept_id)
    if target is None:
        raise TargetConceptNotFoundError(
            f"Target concept {target_concept_id!r} was not found",
            details={"target_concept_id": str(target_concept_id)},
        )
    if not is_approved(target.status):
        raise TargetConceptNotApprovedError(
            f"Target concept {target_concept_id!r} is not teacher-approved",
            details={
                "target_concept_id": str(target_concept_id),
                "status": enum_token(target.status),
            },
        )

    approved_concept_ids = {
        concept_id
        for concept_id, concept in concept_by_id.items()
        if is_approved(concept.status)
    }
    trusted_relations = tuple(
        relation
        for relation in approved_prerequisite_relations(relations)
        if relation.prerequisite_id in approved_concept_ids
        and relation.concept_id in approved_concept_ids
    )
    validate_prerequisite_graph(trusted_relations, approved_only=False)
    ancestor_depth = prerequisite_ancestor_depths(
        target_concept_id,
        trusted_relations,
        max_depth=max_depth,
    )
    selected_ids = set(ancestor_depth)
    selected_ids.add(target_concept_id)

    adjacency: dict[ConceptId, list[ConceptId]] = {
        concept_id: [] for concept_id in selected_ids
    }
    indegree: dict[ConceptId, int] = {concept_id: 0 for concept_id in selected_ids}
    for relation in trusted_relations:
        source = relation.prerequisite_id
        target_id = relation.concept_id
        if source not in selected_ids or target_id not in selected_ids:
            continue
        if target_id in adjacency[source]:
            continue
        adjacency[source].append(target_id)
        indegree[target_id] += 1

    mastery = mastery_by_concept or {}
    mastery_values = {
        concept_id: _mastery_value(concept_id, mastery) for concept_id in selected_ids
    }
    fallback_order = len(concept_order)

    def priority(concept_id: ConceptId) -> tuple[int, int, str]:
        weak_rank = 0 if mastery_values[concept_id] < weak_threshold else 1
        return (
            weak_rank,
            concept_order.get(concept_id, fallback_order),
            str(concept_id),
        )

    ready = [concept_id for concept_id, degree in indegree.items() if degree == 0]
    ordered: list[ConceptId] = []
    while ready:
        ready.sort(key=priority)
        concept_id = ready.pop(0)
        ordered.append(concept_id)
        for dependent_id in adjacency[concept_id]:
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                ready.append(dependent_id)

    if len(ordered) != len(selected_ids):
        # Defensive: the trusted graph was already validated, so this should only
        # be reachable if the ordering implementation changes incorrectly.
        cycle = detect_prerequisite_cycle(trusted_relations, approved_only=False)
        raise PrerequisiteCycleError(cycle or tuple(selected_ids))

    steps = tuple(
        LearningPathStep(
            concept_id=concept_id,
            mastery=mastery_values[concept_id],
            is_weak=mastery_values[concept_id] < weak_threshold,
            depth=ancestor_depth.get(concept_id, 0),
            reason=(
                "TARGET"
                if concept_id == target_concept_id
                else (
                    "WEAK_PREREQUISITE"
                    if mastery_values[concept_id] < weak_threshold
                    else "PREREQUISITE"
                )
            ),
        )
        for concept_id in ordered
    )
    return LearningPath(
        target_concept_id=target_concept_id,
        steps=steps,
        max_depth=max_depth,
    )


def build_learning_path(
    target_concept_id: ConceptId,
    concepts: Iterable[Concept] | Mapping[ConceptId, Concept | ReviewStatus | str],
    relations: Iterable[PrerequisiteRelation],
    mastery_by_concept: Mapping[ConceptId, float | MasteryState] | None = None,
    *,
    max_depth: int = MAX_PREREQUISITE_DEPTH,
    weak_threshold: float = WEAK_MASTERY_THRESHOLD,
) -> tuple[ConceptId, ...]:
    """Convenience API returning only the ordered concept identifiers."""

    return plan_learning_path(
        target_concept_id,
        concepts,
        relations,
        mastery_by_concept,
        max_depth=max_depth,
        weak_threshold=weak_threshold,
    ).concept_ids


has_prerequisite_cycle = detect_prerequisite_cycle
