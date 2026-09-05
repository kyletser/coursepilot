from __future__ import annotations

import asyncio
import math

import pytest

from app.retrieval import (
    BGEM3EmbeddingAdapter,
    BGERerankerAdapter,
    DenseCandidate,
    HybridRetrievalConfig,
    HybridRetriever,
    LexicalCandidate,
    LexicalDocument,
    LexicalIndexArtifactError,
    LexicalIndexArtifactStore,
    LexicalIndexManager,
    LightweightBM25Index,
    RetrievalUnavailableError,
    normalize_text,
    reciprocal_rank_fusion,
    tokenize,
)


def test_mixed_chinese_english_normalization_and_tokenization_are_stable():
    text = "  ＢＧＥ－M3，页面置换！Hello—WORLD\n"
    assert normalize_text(text) == "bge m3 页面置换 hello world"
    assert tokenize(text) == [
        "bge",
        "m3",
        "页",
        "面",
        "置",
        "换",
        "hello",
        "world",
    ]
    assert tokenize("Virtual-Memory") == tokenize("VIRTUAL，MEMORY")


def test_rrf_uses_one_based_ranks_deduplicates_and_applies_top_k():
    dense = [
        DenseCandidate("a", 0.99, "dense a", {"dense": True}),
        DenseCandidate("a", 0.98, "duplicate must not consume a rank"),
        DenseCandidate("b", 0.8, "dense b", {"shared": "dense"}),
    ]
    lexical = [
        LexicalCandidate("b", 12.0, "lexical b", {"shared": "lexical"}),
        LexicalCandidate("c", 11.0, "lexical c"),
    ]

    fused = reciprocal_rank_fusion(dense, lexical, k=60, top_k=2)

    assert [item.chunk_id for item in fused] == ["b", "a"]
    assert fused[0].rrf_score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[0].dense_rank == 2
    assert fused[0].lexical_rank == 1
    assert fused[0].sources == ("dense", "lexical")
    assert fused[0].metadata["shared"] == "dense"
    assert fused[1].rrf_score == pytest.approx(1 / 61)


def test_lightweight_bm25_searches_chinese_and_english_consistently():
    index = LightweightBM25Index(
        [
            LexicalDocument(
                "os-1", "页面置换算法 Page Replacement Algorithm", {"page": 42}
            ),
            LexicalDocument("ds-1", "A stack uses last-in first-out order"),
            LexicalDocument("os-2", "进程调度与上下文切换"),
        ]
    )

    chinese = index.search("什么是页面置换？", top_k=2)
    english = index.search("PAGE, replacement!", top_k=2)

    assert chinese[0].chunk_id == "os-1"
    assert english[0].chunk_id == "os-1"
    assert chinese[0].metadata["page"] == 42
    assert 0.0 <= chinese[0].metadata["normalized_score"] <= 1.0
    assert all(math.isfinite(candidate.score) for candidate in chinese + english)
    assert index.search("不存在的术语 xyzzy", top_k=5) == []


def test_lightweight_bm25_normalized_score_discriminates_partial_query_coverage():
    index = LightweightBM25Index(
        [
            LexicalDocument("full", "alpha beta"),
            LexicalDocument("partial", "alpha"),
        ]
    )

    hits = index.search("alpha beta", top_k=2)
    by_chunk = {hit.chunk_id: hit for hit in hits}

    # The document containing every query term covers ~all of the query's
    # achievable BM25 mass; the document matching only one of two terms must
    # stay below EvidencePolicy.minimum_score instead of passing by rank.
    assert by_chunk["full"].metadata["normalized_score"] > 0.55
    assert by_chunk["partial"].metadata["normalized_score"] < 0.55
    # Raw BM25 stays available for traces and fusion ordering.
    assert by_chunk["full"].score > 0
    assert [hit.chunk_id for hit in hits] == ["full", "partial"]


def test_course_version_artifact_save_and_replace_are_atomic(tmp_path):
    store = LexicalIndexArtifactStore(tmp_path)
    old = LightweightBM25Index([LexicalDocument("old", "旧版 页面")])
    new = LightweightBM25Index([LexicalDocument("new", "新版 页面")])

    artifact = store.save("course-a", "v1", old)
    assert artifact == tmp_path / "course-a" / "v1" / "lexical-index.json"
    assert store.load("course-a", "v1").search("旧版")[0].chunk_id == "old"

    assert store.replace("course-a", "v1", new) == artifact
    loaded = store.load("course-a", "v1")
    assert loaded.search("新版")[0].chunk_id == "new"
    assert loaded.search("旧") == []
    assert list(artifact.parent.glob("*.tmp")) == []

    with pytest.raises(ValueError):
        store.artifact_path("../another-course", "v1")


def test_failed_publication_keeps_previous_cached_index(monkeypatch, tmp_path):
    manager = LexicalIndexManager(tmp_path)
    manager.publish("course-a", "v1", [LexicalDocument("old", "stable evidence")])

    def fail_save(*args, **kwargs):
        raise LexicalIndexArtifactError("disk full")

    monkeypatch.setattr(manager.store, "replace", fail_save)
    with pytest.raises(LexicalIndexArtifactError, match="disk full"):
        manager.publish("course-a", "v1", [LexicalDocument("new", "partial evidence")])

    assert (
        manager.search(course_id="course-a", index_version="v1", query="stable")[
            0
        ].chunk_id
        == "old"
    )


class _FakeEmbeddingModel:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def encode(self, texts, **kwargs):
        self.calls.append(texts)
        return [[float(len(text)), 1.0] for text in texts]


class _FakeRerankerModel:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def predict(self, pairs, **kwargs):
        self.calls.append(pairs)
        return [float(len(document)) for _, document in pairs]


async def test_bge_adapters_are_lazy_and_support_injected_local_models():
    embedding_models: list[_FakeEmbeddingModel] = []
    reranker_models: list[_FakeRerankerModel] = []

    def make_embedding():
        model = _FakeEmbeddingModel()
        embedding_models.append(model)
        return model

    def make_reranker():
        model = _FakeRerankerModel()
        reranker_models.append(model)
        return model

    embedding = BGEM3EmbeddingAdapter(model_factory=make_embedding)
    reranker = BGERerankerAdapter(model_factory=make_reranker)
    assert not embedding.is_loaded
    assert not reranker.is_loaded
    assert embedding_models == reranker_models == []

    assert await embedding.embed_query("页面") == [2.0, 1.0]
    assert await embedding.embed_documents(["a", "abcd"]) == [
        [1.0, 1.0],
        [4.0, 1.0],
    ]
    assert await reranker.score("q", ["a", "long"]) == [1.0, 4.0]
    assert embedding.is_loaded and reranker.is_loaded
    assert len(embedding_models) == len(reranker_models) == 1


class _StaticDense:
    def __init__(self, candidates):
        self.candidates = candidates

    async def search(self, **kwargs):
        return self.candidates


class _StaticLexical:
    def __init__(self, candidates):
        self.candidates = candidates

    async def search(self, **kwargs):
        return self.candidates


class _ReverseReranker:
    async def score(self, query, documents):
        return list(range(len(documents)))


async def test_hybrid_retrieval_reranks_top_k_and_records_complete_trace():
    retriever = HybridRetriever(
        dense=_StaticDense(
            [
                DenseCandidate("a", 0.9, "first"),
                DenseCandidate("b", 0.8, "second"),
            ]
        ),
        lexical=_StaticLexical(
            [
                LexicalCandidate("b", 8.0, "second"),
                LexicalCandidate("c", 7.0, "third"),
            ]
        ),
        reranker=_ReverseReranker(),
        config=HybridRetrievalConfig(final_top_k=2),
    )

    result = await retriever.retrieve(
        course_id="course-a",
        index_version="v7",
        query="memory",
        trace_id="trace-123",
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["c", "a"]
    assert all(candidate.rerank_score is not None for candidate in result.candidates)
    trace = result.trace
    assert trace.trace_id == "trace-123"
    assert trace.index_version == "v7"
    assert trace.dense.status == trace.lexical.status == "success"
    assert (
        trace.fusion.status == trace.reranker.status == trace.final.status == "success"
    )
    assert not trace.degraded
    assert [item.chunk_id for item in trace.fusion.candidates] == ["b", "a", "c"]
    assert trace.to_dict()["stages"]["final"]["candidates"][0]["chunk_id"] == "c"


class _SlowReranker:
    async def score(self, query, documents):
        await asyncio.sleep(0.05)
        return [100.0 for _ in documents]


async def test_reranker_timeout_falls_back_to_exact_rrf_order():
    retriever = HybridRetriever(
        dense=_StaticDense(
            [DenseCandidate("a", 0.9, "a"), DenseCandidate("b", 0.8, "b")]
        ),
        lexical=_StaticLexical([LexicalCandidate("b", 8.0, "b")]),
        reranker=_SlowReranker(),
        config=HybridRetrievalConfig(
            final_top_k=2,
            reranker_timeout_seconds=0.001,
        ),
    )

    result = await retriever.retrieve(
        course_id="course-a", index_version="v1", query="q"
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["b", "a"]
    assert all(candidate.rerank_score is None for candidate in result.candidates)
    assert result.trace.reranker.status == "timeout_fallback"
    assert result.trace.degraded_routes == ["reranker_timeout"]


class _FailingDense:
    async def search(self, **kwargs):
        raise ConnectionError("pgvector unavailable")


async def test_one_route_failure_degrades_to_the_other_route():
    retriever = HybridRetriever(
        dense=_FailingDense(),
        lexical=_StaticLexical([LexicalCandidate("lexical-only", 4.0, "text")]),
    )

    result = await retriever.retrieve(
        course_id="course-a", index_version="v1", query="q"
    )

    assert [candidate.chunk_id for candidate in result.candidates] == ["lexical-only"]
    assert result.trace.dense.status == "failed"
    assert result.trace.lexical.status == "success"
    assert result.trace.degraded_routes == ["dense"]
    assert result.candidates[0].sources == ("lexical",)


async def test_both_routes_unavailable_raise_with_actionable_trace():
    retriever = HybridRetriever(dense=_FailingDense(), lexical=None)

    with pytest.raises(RetrievalUnavailableError) as captured:
        await retriever.retrieve(course_id="course-a", index_version="v1", query="q")

    error = captured.value
    assert error.code == "RETRIEVAL_UNAVAILABLE"
    assert error.trace.dense.status == "failed"
    assert error.trace.lexical.status == "unavailable"
    assert error.trace.final.status == "failed"
    assert error.trace.degraded_routes == ["dense", "lexical"]
    assert error.trace.total_latency_ms >= 0


async def test_successful_empty_routes_are_not_misreported_as_unavailable():
    retriever = HybridRetriever(
        dense=_StaticDense([]),
        lexical=_StaticLexical([]),
    )

    result = await retriever.retrieve(
        course_id="course-a", index_version="v1", query="no evidence"
    )

    assert result.candidates == ()
    assert result.trace.dense.status == result.trace.lexical.status == "success"
    assert result.trace.reranker.status == "skipped_empty"
    assert not result.trace.degraded
