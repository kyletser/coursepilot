#!/usr/bin/env python3
"""Evaluate CoursePilot retrieval on a deterministic CMRC 2018 subset.

This is an external-domain transfer check, not an official CMRC leaderboard run.
The script consumes an explicitly supplied upstream JSON file and never downloads
data or calls an external model API.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
API_ROOT = REPOSITORY_ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.config import get_settings
from app.evaluation.metrics import RetrievalCase, evaluate_retrieval
from app.retrieval import (
    BGEM3EmbeddingAdapter,
    BGERerankerAdapter,
    DenseCandidate,
    HybridRetrievalConfig,
    HybridRetriever,
    LexicalDocument,
    LightweightBM25Index,
)

UPSTREAM_URL = "https://github.com/ymcui/cmrc2018"
UPSTREAM_LICENSE = "CC-BY-SA-4.0"
REPORT_SCHEMA = "coursepilot.external-retrieval-report/1.0.0"


@dataclass(frozen=True, slots=True)
class ExternalCase:
    context_id: str
    title: str
    context: str
    query_id: str
    query: str
    answers: tuple[str, ...]


def _git_provenance(*, allow_dirty: bool) -> tuple[str, bool]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    if dirty and not allow_dirty:
        raise RuntimeError("External formal evaluation requires a clean worktree")
    return commit, dirty


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_cases(path: Path, count: int) -> list[ExternalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise TypeError("CMRC trial data must be a JSON array")
    cases: list[ExternalCase] = []
    for item in sorted(payload, key=lambda value: str(value.get("context_id", ""))):
        if not isinstance(item, dict):
            continue
        context_id = str(item.get("context_id", "")).strip()
        context = str(item.get("context_text", "")).strip()
        qas = item.get("qas")
        if not context_id or not context or not isinstance(qas, list) or not qas:
            continue
        qa = qas[0]
        if not isinstance(qa, dict):
            continue
        query_id = str(qa.get("query_id", "")).strip()
        query = str(qa.get("query_text", "")).strip()
        raw_answers = qa.get("answers")
        if not query_id or not query or not isinstance(raw_answers, list):
            continue
        answers = tuple(
            str(answer).strip() for answer in raw_answers if str(answer).strip()
        )
        if not answers:
            continue
        cases.append(
            ExternalCase(
                context_id=context_id,
                title=str(item.get("title", "")).strip(),
                context=context,
                query_id=query_id,
                query=query,
                answers=answers,
            )
        )
        if len(cases) == count:
            return cases
    raise ValueError(
        f"CMRC source only supplied {len(cases)} usable cases; need {count}"
    )


class _MemoryDenseRetriever:
    def __init__(self, cases: list[ExternalCase], vectors: list[list[float]]) -> None:
        self.cases = cases
        self.vectors = vectors

    async def search(self, *, query: str, top_k: int, **_kwargs: Any):
        query_vector = await self.embedding.embed_query(query)
        ranked = sorted(
            zip(self.cases, self.vectors, strict=True),
            key=lambda item: (
                -math.fsum(
                    left * right
                    for left, right in zip(query_vector, item[1], strict=True)
                )
            ),
        )[:top_k]
        return [
            DenseCandidate(
                case.context_id,
                math.fsum(
                    left * right
                    for left, right in zip(query_vector, vector, strict=True)
                ),
                content=case.context,
            )
            for case, vector in ranked
        ]

    embedding: BGEM3EmbeddingAdapter


class _MemoryLexicalRetriever:
    def __init__(self, index: LightweightBM25Index) -> None:
        self.index = index

    def search(self, *, query: str, top_k: int, **_kwargs: Any):
        return self.index.search(query, top_k=top_k)


async def _run(args: argparse.Namespace) -> Path:
    if not 2 <= args.sample_count <= 500:
        raise ValueError("sample-count must be between 2 and 500")
    if not args.sample_count <= args.candidate_count <= 1000:
        raise ValueError("candidate-count must be between sample-count and 1000")
    source = args.source.resolve()
    commit, dirty = _git_provenance(allow_dirty=args.allow_dirty)
    corpus_cases = _load_cases(source, args.candidate_count)
    evaluation_cases = corpus_cases[: args.sample_count]
    settings = get_settings()
    embedding = BGEM3EmbeddingAdapter(
        settings.embedding_model,
        allow_download=settings.model_allow_download,
        cache_folder=settings.hf_hub_cache,
    )
    contexts = [case.context for case in corpus_cases]
    build_started = time.perf_counter()
    vectors = await embedding.embed_documents(contexts)
    dense = _MemoryDenseRetriever(corpus_cases, vectors)
    dense.embedding = embedding
    lexical = _MemoryLexicalRetriever(
        LightweightBM25Index(
            LexicalDocument(case.context_id, case.context) for case in corpus_cases
        )
    )
    reranker = BGERerankerAdapter(
        settings.reranker_model,
        allow_download=settings.model_allow_download,
        cache_folder=settings.hf_hub_cache,
    )
    build_ms = (time.perf_counter() - build_started) * 1000

    outputs: list[dict[str, Any]] = []
    for mode in ("dense_only", "hybrid_rerank"):
        retriever = HybridRetriever(
            dense=dense,
            lexical=lexical if mode == "hybrid_rerank" else None,
            reranker=reranker if mode == "hybrid_rerank" else None,
            config=HybridRetrievalConfig(
                route_top_k=10,
                fusion_top_k=8,
                final_top_k=5,
                reranker_timeout_seconds=15.0,
            ),
        )
        started = time.perf_counter()
        case_results: dict[str, Any] = {}
        metric_cases: list[RetrievalCase] = []
        for case in evaluation_cases:
            result = await retriever.retrieve(
                course_id="cmrc2018-external",
                index_version=args.source_commit,
                query=case.query,
                top_k=5,
                trace_id=case.query_id,
            )
            ranked = tuple(candidate.chunk_id for candidate in result.candidates)
            metric_cases.append(RetrievalCase(ranked, frozenset({case.context_id})))
            case_results[case.query_id] = {
                "context_id": case.context_id,
                "title": case.title,
                "query": case.query,
                "answers": list(case.answers),
                "ranked_context_ids": list(ranked),
                "retrieval_trace": result.trace.to_dict(),
            }
        duration_ms = (time.perf_counter() - started) * 1000
        outputs.append(
            {
                "mode": mode,
                "metrics": evaluate_retrieval(metric_cases).to_dict(),
                "duration_ms": round(duration_ms, 3),
                "latency_ms_per_query": round(duration_ms / len(evaluation_cases), 3),
                "case_results": case_results,
            }
        )

    report = {
        "schema_version": REPORT_SCHEMA,
        "scope": "external-domain retrieval transfer check; not an official CMRC score",
        "provenance": {
            "git_commit": commit,
            "git_dirty": dirty,
            "source_repository": UPSTREAM_URL,
            "source_commit": args.source_commit,
            "source_file": source.name,
            "source_sha256": _source_sha256(source),
            "license": UPSTREAM_LICENSE,
            "selection": (
                "sort context_id ascending; first QA from first sample_count usable "
                "contexts; retrieve against first candidate_count usable contexts"
            ),
        },
        "models": {
            "embedding": settings.embedding_model,
            "reranker": settings.reranker_model,
            "external_api_used": False,
        },
        "hardware": {
            "platform": platform.platform(),
            "processor": platform.processor(),
            "python": platform.python_version(),
        },
        "dataset": {
            "sample_count": len(evaluation_cases),
            "candidate_passage_count": len(corpus_cases),
        },
        "index_build_ms": round(build_ms, 3),
        "baselines": outputs,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    return args.output


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--sample-count", type=int, default=50)
    parser.add_argument("--candidate-count", type=int, default=200)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--allow-dirty", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    result_path = asyncio.run(_run(_parse_args()))
    print(result_path)
