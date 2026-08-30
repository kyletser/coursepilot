from __future__ import annotations

import math
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any


def _require_positive_k(k: int) -> None:
    if isinstance(k, bool) or not isinstance(k, int) or k <= 0:
        raise ValueError("k must be a positive integer")


def _normalize_ids(values: Iterable[str], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must contain non-blank strings")
        if value in seen:
            continue
        normalized.append(value)
        seen.add(value)
    return tuple(normalized)


def _normalize_relevant(values: Collection[str]) -> frozenset[str]:
    relevant = frozenset(_normalize_ids(values, field_name="relevant ids"))
    if not relevant:
        raise ValueError("at least one relevant id is required")
    return relevant


def recall_at_k(
    ranked_ids: Sequence[str], relevant_ids: Collection[str], k: int = 5
) -> float:
    """Return binary recall for one query after de-duplicating the ranking."""

    _require_positive_k(k)
    ranked = _normalize_ids(ranked_ids, field_name="ranked ids")
    relevant = _normalize_relevant(relevant_ids)
    return len(set(ranked[:k]).intersection(relevant)) / len(relevant)


def reciprocal_rank_at_k(
    ranked_ids: Sequence[str], relevant_ids: Collection[str], k: int = 5
) -> float:
    _require_positive_k(k)
    ranked = _normalize_ids(ranked_ids, field_name="ranked ids")
    relevant = _normalize_relevant(relevant_ids)
    for rank, item_id in enumerate(ranked[:k], start=1):
        if item_id in relevant:
            return 1.0 / rank
    return 0.0


def mrr_at_k(
    rankings: Sequence[Sequence[str]],
    relevant_ids_by_query: Sequence[Collection[str]],
    k: int = 5,
) -> float:
    """Return mean reciprocal rank across queries."""

    _require_positive_k(k)
    if len(rankings) != len(relevant_ids_by_query):
        raise ValueError("rankings and relevance labels must have equal lengths")
    if not rankings:
        raise ValueError("at least one retrieval case is required")
    return sum(
        reciprocal_rank_at_k(ranked, relevant, k)
        for ranked, relevant in zip(rankings, relevant_ids_by_query, strict=True)
    ) / len(rankings)


def _normalize_relevance(
    relevance: Mapping[str, float] | Collection[str],
) -> Mapping[str, float]:
    if isinstance(relevance, Mapping):
        normalized: dict[str, float] = {}
        for item_id, grade in relevance.items():
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError("relevance ids must be non-blank strings")
            if isinstance(grade, bool) or not isinstance(grade, (int, float)):
                raise TypeError("relevance grades must be numbers")
            numeric_grade = float(grade)
            if not math.isfinite(numeric_grade) or numeric_grade < 0:
                raise ValueError("relevance grades must be finite and non-negative")
            if numeric_grade > 0:
                normalized[item_id] = numeric_grade
    else:
        normalized = {
            item_id: 1.0
            for item_id in _normalize_ids(relevance, field_name="relevant ids")
        }
    if not normalized:
        raise ValueError("at least one positively relevant id is required")
    return MappingProxyType(normalized)


def _discounted_gain(grades: Sequence[float]) -> float:
    return sum(
        ((2.0**grade) - 1.0) / math.log2(rank + 1)
        for rank, grade in enumerate(grades, start=1)
    )


def ndcg_at_k(
    ranked_ids: Sequence[str],
    relevance: Mapping[str, float] | Collection[str],
    k: int = 10,
) -> float:
    """Return nDCG for one query using graded or binary relevance."""

    _require_positive_k(k)
    ranked = _normalize_ids(ranked_ids, field_name="ranked ids")
    grades = _normalize_relevance(relevance)
    actual = [grades.get(item_id, 0.0) for item_id in ranked[:k]]
    ideal = sorted(grades.values(), reverse=True)[:k]
    ideal_dcg = _discounted_gain(ideal)
    return _discounted_gain(actual) / ideal_dcg


@dataclass(frozen=True, slots=True)
class RetrievalCase:
    ranked_chunk_ids: tuple[str, ...]
    relevant_chunk_ids: frozenset[str]
    relevance_grades: Mapping[str, float] | None = None

    def __post_init__(self) -> None:
        ranked = _normalize_ids(self.ranked_chunk_ids, field_name="ranked chunk ids")
        relevant = _normalize_relevant(self.relevant_chunk_ids)
        object.__setattr__(self, "ranked_chunk_ids", ranked)
        object.__setattr__(self, "relevant_chunk_ids", relevant)
        if self.relevance_grades is not None:
            grades = _normalize_relevance(self.relevance_grades)
            if set(grades) != set(relevant):
                raise ValueError(
                    "relevance_grades must contain exactly the relevant chunk ids"
                )
            object.__setattr__(self, "relevance_grades", grades)


@dataclass(frozen=True, slots=True)
class RetrievalMetrics:
    recall_at_5: float
    mrr_at_5: float
    ndcg_at_10: float
    case_count: int

    def to_dict(self) -> dict[str, float | int]:
        return {
            "recall_at_5": self.recall_at_5,
            "mrr_at_5": self.mrr_at_5,
            "ndcg_at_10": self.ndcg_at_10,
            "case_count": self.case_count,
        }


def evaluate_retrieval(cases: Iterable[RetrievalCase]) -> RetrievalMetrics:
    normalized = tuple(cases)
    if not normalized:
        raise ValueError("at least one retrieval case is required")
    recall = sum(
        recall_at_k(case.ranked_chunk_ids, case.relevant_chunk_ids, 5)
        for case in normalized
    ) / len(normalized)
    mrr = mrr_at_k(
        [case.ranked_chunk_ids for case in normalized],
        [case.relevant_chunk_ids for case in normalized],
        5,
    )
    ndcg = sum(
        ndcg_at_k(
            case.ranked_chunk_ids,
            case.relevance_grades or case.relevant_chunk_ids,
            10,
        )
        for case in normalized
    ) / len(normalized)
    return RetrievalMetrics(recall, mrr, ndcg, len(normalized))


@dataclass(frozen=True, slots=True)
class CitationJudgment:
    claim_id: str
    citation_id: str
    citation_exists: bool
    belongs_to_expected_scope: bool
    supports_claim: bool

    def __post_init__(self) -> None:
        if not self.claim_id.strip() or not self.citation_id.strip():
            raise ValueError("claim_id and citation_id must not be blank")

    @property
    def is_accurate(self) -> bool:
        return (
            self.citation_exists
            and self.belongs_to_expected_scope
            and self.supports_claim
        )


@dataclass(frozen=True, slots=True)
class CitationCase:
    required_claim_ids: frozenset[str]
    citations: tuple[CitationJudgment, ...]

    def __post_init__(self) -> None:
        required = frozenset(
            _normalize_ids(self.required_claim_ids, field_name="required claim ids")
        )
        citations = tuple(self.citations)
        seen: set[tuple[str, str]] = set()
        for citation in citations:
            key = (citation.claim_id, citation.citation_id)
            if key in seen:
                raise ValueError("duplicate citation judgment")
            seen.add(key)
        object.__setattr__(self, "required_claim_ids", required)
        object.__setattr__(self, "citations", citations)


@dataclass(frozen=True, slots=True)
class CitationMetrics:
    citation_accuracy: float | None
    citation_coverage: float | None
    accurate_citation_count: int
    citation_count: int
    covered_claim_count: int
    required_claim_count: int
    case_count: int

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "citation_accuracy": self.citation_accuracy,
            "citation_coverage": self.citation_coverage,
            "accurate_citation_count": self.accurate_citation_count,
            "citation_count": self.citation_count,
            "covered_claim_count": self.covered_claim_count,
            "required_claim_count": self.required_claim_count,
            "case_count": self.case_count,
        }


def evaluate_citations(cases: Iterable[CitationCase]) -> CitationMetrics:
    normalized = tuple(cases)
    if not normalized:
        raise ValueError("at least one citation case is required")
    citations = tuple(citation for case in normalized for citation in case.citations)
    accurate_count = sum(citation.is_accurate for citation in citations)
    required_count = sum(len(case.required_claim_ids) for case in normalized)
    covered_count = sum(
        len(
            case.required_claim_ids.intersection(
                citation.claim_id for citation in case.citations
            )
        )
        for case in normalized
    )
    accuracy = accurate_count / len(citations) if citations else None
    coverage = covered_count / required_count if required_count else None
    return CitationMetrics(
        accuracy,
        coverage,
        accurate_count,
        len(citations),
        covered_count,
        required_count,
        len(normalized),
    )


@dataclass(frozen=True, slots=True)
class AbstentionCase:
    is_answerable: bool
    refused: bool


@dataclass(frozen=True, slots=True)
class AbstentionMetrics:
    unanswerable_refusal_rate: float | None
    false_refusal_rate: float | None
    refused_unanswerable_count: int
    unanswerable_count: int
    falsely_refused_count: int
    answerable_count: int
    case_count: int

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "unanswerable_refusal_rate": self.unanswerable_refusal_rate,
            "false_refusal_rate": self.false_refusal_rate,
            "refused_unanswerable_count": self.refused_unanswerable_count,
            "unanswerable_count": self.unanswerable_count,
            "falsely_refused_count": self.falsely_refused_count,
            "answerable_count": self.answerable_count,
            "case_count": self.case_count,
        }


def evaluate_abstention(cases: Iterable[AbstentionCase]) -> AbstentionMetrics:
    normalized = tuple(cases)
    if not normalized:
        raise ValueError("at least one abstention case is required")
    unanswerable = [case for case in normalized if not case.is_answerable]
    answerable = [case for case in normalized if case.is_answerable]
    refused_unanswerable = sum(case.refused for case in unanswerable)
    falsely_refused = sum(case.refused for case in answerable)
    return AbstentionMetrics(
        refused_unanswerable / len(unanswerable) if unanswerable else None,
        falsely_refused / len(answerable) if answerable else None,
        refused_unanswerable,
        len(unanswerable),
        falsely_refused,
        len(answerable),
        len(normalized),
    )


def _normalize_label(value: str, *, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-blank string")
    return value


@dataclass(frozen=True, slots=True)
class RoutingCase:
    expected_intent: str
    predicted_intent: str

    def __post_init__(self) -> None:
        _normalize_label(self.expected_intent, field_name="expected_intent")
        _normalize_label(self.predicted_intent, field_name="predicted_intent")


@dataclass(frozen=True, slots=True)
class RoutingMetrics:
    macro_f1: float
    confusion_matrix: Mapping[str, Mapping[str, int]]
    labels: tuple[str, ...]
    case_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "macro_f1": self.macro_f1,
            "confusion_matrix": {
                actual: dict(predicted_counts)
                for actual, predicted_counts in self.confusion_matrix.items()
            },
            "labels": list(self.labels),
            "case_count": self.case_count,
        }


def evaluate_routing(
    cases: Iterable[RoutingCase], labels: Sequence[str] | None = None
) -> RoutingMetrics:
    normalized = tuple(cases)
    if not normalized:
        raise ValueError("at least one routing case is required")
    observed = {
        intent
        for case in normalized
        for intent in (case.expected_intent, case.predicted_intent)
    }
    if labels is None:
        ordered_labels = tuple(sorted(observed))
    else:
        ordered_labels = _normalize_ids(labels, field_name="routing labels")
        if not observed.issubset(ordered_labels):
            missing = sorted(observed.difference(ordered_labels))
            raise ValueError(f"routing labels omit observed intents: {missing}")
    matrix: dict[str, dict[str, int]] = {
        actual: {predicted: 0 for predicted in ordered_labels}
        for actual in ordered_labels
    }
    for case in normalized:
        matrix[case.expected_intent][case.predicted_intent] += 1

    f1_scores: list[float] = []
    for label in ordered_labels:
        true_positive = matrix[label][label]
        false_positive = sum(
            matrix[actual][label] for actual in ordered_labels if actual != label
        )
        false_negative = sum(
            matrix[label][predicted]
            for predicted in ordered_labels
            if predicted != label
        )
        denominator = (2 * true_positive) + false_positive + false_negative
        f1_scores.append((2 * true_positive) / denominator if denominator else 0.0)

    frozen_matrix = MappingProxyType(
        {
            actual: MappingProxyType(predicted_counts)
            for actual, predicted_counts in matrix.items()
        }
    )
    return RoutingMetrics(
        sum(f1_scores) / len(f1_scores),
        frozen_matrix,
        ordered_labels,
        len(normalized),
    )


@dataclass(frozen=True, slots=True)
class PathCase:
    predicted_concept_ids: tuple[str, ...]
    approved_prerequisite_edges: frozenset[tuple[str, str]]
    satisfied_prerequisite_ids: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        path = _normalize_ids(
            self.predicted_concept_ids, field_name="predicted concept ids"
        )
        if len(path) != len(self.predicted_concept_ids):
            raise ValueError("a learning path cannot contain duplicate concepts")
        if not path:
            raise ValueError("a learning path must contain at least one concept")
        edges: set[tuple[str, str]] = set()
        for edge in self.approved_prerequisite_edges:
            if len(edge) != 2:
                raise ValueError("each prerequisite edge must contain two concepts")
            prerequisite, dependent = edge
            _normalize_label(prerequisite, field_name="prerequisite concept")
            _normalize_label(dependent, field_name="dependent concept")
            if prerequisite == dependent:
                raise ValueError("prerequisite edges cannot be self-loops")
            edges.add((prerequisite, dependent))
        satisfied = frozenset(
            _normalize_ids(
                self.satisfied_prerequisite_ids,
                field_name="satisfied prerequisite ids",
            )
        )
        object.__setattr__(self, "predicted_concept_ids", path)
        object.__setattr__(self, "approved_prerequisite_edges", frozenset(edges))
        object.__setattr__(self, "satisfied_prerequisite_ids", satisfied)


@dataclass(frozen=True, slots=True)
class PathMetrics:
    prerequisite_legality_rate: float | None
    legal_dependency_count: int
    evaluated_dependency_count: int
    fully_legal_path_rate: float | None
    fully_legal_path_count: int
    constrained_path_count: int
    case_count: int

    def to_dict(self) -> dict[str, float | int | None]:
        return {
            "prerequisite_legality_rate": self.prerequisite_legality_rate,
            "legal_dependency_count": self.legal_dependency_count,
            "evaluated_dependency_count": self.evaluated_dependency_count,
            "fully_legal_path_rate": self.fully_legal_path_rate,
            "fully_legal_path_count": self.fully_legal_path_count,
            "constrained_path_count": self.constrained_path_count,
            "case_count": self.case_count,
        }


def evaluate_paths(cases: Iterable[PathCase]) -> PathMetrics:
    normalized = tuple(cases)
    if not normalized:
        raise ValueError("at least one learning-path case is required")
    total_dependencies = 0
    legal_dependencies = 0
    constrained_paths = 0
    fully_legal_paths = 0

    for case in normalized:
        positions = {
            concept_id: position
            for position, concept_id in enumerate(case.predicted_concept_ids)
        }
        case_total = 0
        case_legal = 0
        for prerequisite, dependent in case.approved_prerequisite_edges:
            if dependent not in positions:
                continue
            case_total += 1
            if prerequisite in positions:
                is_legal = positions[prerequisite] < positions[dependent]
            else:
                is_legal = prerequisite in case.satisfied_prerequisite_ids
            case_legal += is_legal
        total_dependencies += case_total
        legal_dependencies += case_legal
        if case_total:
            constrained_paths += 1
            fully_legal_paths += case_legal == case_total

    return PathMetrics(
        legal_dependencies / total_dependencies if total_dependencies else None,
        legal_dependencies,
        total_dependencies,
        fully_legal_paths / constrained_paths if constrained_paths else None,
        fully_legal_paths,
        constrained_paths,
        len(normalized),
    )


# Concise aliases for callers that group cases by metric family.
citation_metrics = evaluate_citations
abstention_metrics = evaluate_abstention
routing_metrics = evaluate_routing
path_metrics = evaluate_paths
