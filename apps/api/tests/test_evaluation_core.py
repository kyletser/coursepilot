from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.evaluation import (
    REPORT_SCHEMA_VERSION,
    AbstentionCase,
    CitationCase,
    CitationJudgment,
    DatasetFrozenError,
    EvalConfig,
    EvalDataset,
    PathCase,
    RetrievalCase,
    RoutingCase,
    build_evaluation_report,
    evaluate_abstention,
    evaluate_citations,
    evaluate_paths,
    evaluate_retrieval,
    evaluate_routing,
    ndcg_at_k,
)


def test_retrieval_metrics_are_deterministic() -> None:
    cases = [
        RetrievalCase(("a", "x", "b"), frozenset({"a", "b"})),
        RetrievalCase(("x", "y", "c"), frozenset({"c", "d"})),
    ]

    result = evaluate_retrieval(cases)

    assert result.recall_at_5 == pytest.approx(0.75)
    assert result.mrr_at_5 == pytest.approx(2 / 3)
    assert result.ndcg_at_10 == pytest.approx(
        (
            ndcg_at_k(("a", "x", "b"), {"a", "b"}, 10)
            + ndcg_at_k(("x", "y", "c"), {"c", "d"}, 10)
        )
        / 2
    )


def test_citation_and_abstention_metrics_keep_denominators_visible() -> None:
    citation_result = evaluate_citations(
        [
            CitationCase(
                required_claim_ids=frozenset({"claim-1", "claim-2"}),
                citations=(
                    CitationJudgment("claim-1", "cite-1", True, True, True),
                    CitationJudgment("claim-2", "cite-2", True, True, False),
                    CitationJudgment("extra", "cite-3", False, False, False),
                ),
            )
        ]
    )
    abstention_result = evaluate_abstention(
        [
            AbstentionCase(is_answerable=False, refused=True),
            AbstentionCase(is_answerable=False, refused=False),
            AbstentionCase(is_answerable=True, refused=True),
            AbstentionCase(is_answerable=True, refused=False),
        ]
    )

    assert citation_result.citation_accuracy == pytest.approx(1 / 3)
    assert citation_result.citation_coverage == 1.0
    assert citation_result.citation_count == 3
    assert abstention_result.unanswerable_refusal_rate == 0.5
    assert abstention_result.false_refusal_rate == 0.5


def test_routing_and_path_metrics_expose_audit_details() -> None:
    routing = evaluate_routing(
        [
            RoutingCase("A", "A"),
            RoutingCase("A", "B"),
            RoutingCase("B", "B"),
            RoutingCase("C", "B"),
        ],
        labels=("A", "B", "C"),
    )
    paths = evaluate_paths(
        [
            PathCase(
                ("pre", "target"),
                frozenset({("pre", "target"), ("missing", "target")}),
            ),
            PathCase(
                ("target", "pre"),
                frozenset({("pre", "target")}),
            ),
        ]
    )

    assert routing.macro_f1 == pytest.approx(7 / 18)
    assert routing.confusion_matrix["A"]["B"] == 1
    assert routing.confusion_matrix["C"]["B"] == 1
    assert paths.prerequisite_legality_rate == pytest.approx(1 / 3)
    assert paths.evaluated_dependency_count == 3


def test_frozen_dataset_rejects_all_domain_mutations() -> None:
    source_payload = {"query": "栈是什么？", "labels": ["chunk-1"]}
    dataset = EvalDataset(
        dataset_id="retrieval-ds",
        dataset_type="RETRIEVAL",
        version="v1",
        metadata={"course": "data-structures"},
    )
    dataset.add_case("case-1", source_payload)
    source_payload["query"] = "external mutation"
    frozen = dataset.freeze(frozen_at=datetime(2026, 8, 30, tzinfo=UTC))

    assert frozen.cases[0].payload["query"] == "栈是什么？"
    with pytest.raises(TypeError):
        frozen.cases[0].payload["query"] = "blocked"  # type: ignore[index]
    with pytest.raises(DatasetFrozenError):
        dataset.add_case("case-2", {"query": "queue"})
    with pytest.raises(DatasetFrozenError):
        dataset.replace_case("case-1", {"query": "queue"})
    with pytest.raises(DatasetFrozenError):
        dataset.remove_case("case-1")
    with pytest.raises(DatasetFrozenError):
        dataset.replace_metadata({"course": "other"})


def test_versioned_json_report_contains_only_measured_results() -> None:
    dataset = EvalDataset(
        dataset_id="routing-ds",
        dataset_type="INTENT_ROUTING",
        version="2026-08-v1",
    )
    dataset.add_case("route-1", {"input": "给我出题", "expected": "QUIZ"})
    frozen = dataset.freeze(frozen_at=datetime(2026, 8, 30, tzinfo=UTC))
    config = EvalConfig(
        git_commit="0123456789abcdef",
        dataset_version="2026-08-v1",
        index_version="index-v3",
        model_versions={
            "router": "rules-v1",
            "embedding": "BAAI/bge-m3",
            "reranker": "BAAI/bge-reranker-v2-m3",
        },
        prompt_version="router-prompt-v2",
        retrieval_parameters={"dense_top_k": 20, "rrf_k": 60},
        hardware={"cpu": "test-cpu", "accelerator": None},
    )
    measured_routing = evaluate_routing([RoutingCase("QUIZ", "QUIZ")])

    report = build_evaluation_report(
        report_version="baseline-v1",
        generated_at=datetime(2026, 8, 30, 8, 0, tzinfo=UTC),
        dataset=frozen,
        config=config,
        routing=measured_routing,
    )
    payload = json.loads(report.to_json())

    assert payload["schema_version"] == REPORT_SCHEMA_VERSION
    assert payload["dataset"]["content_sha256"] == frozen.content_sha256
    assert payload["config"]["git_commit"] == "0123456789abcdef"
    assert payload["metrics"] == {"routing": measured_routing.to_dict()}
    assert report.to_json() == report.to_json()
