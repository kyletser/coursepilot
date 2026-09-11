from __future__ import annotations

import argparse
import asyncio
import copy
import json
import math
import os
import platform
import subprocess
import sys
import time
import uuid
from collections import deque
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.agent.adapters import build_openai_chat_adapter
from app.agent.core import TrustedAgentCore
from app.agent.grounding import lexical_claim_support
from app.agent.routing import ValidatedIntentRouter
from app.agent.schemas import AgentRequest, AgentStatus, Intent
from app.agent.service import CourseMaterialEvidenceRetriever
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.evaluation.service import _active_cases, _domain_snapshot, create_run
from app.learning.prerequisites import (
    MAX_PREREQUISITE_DEPTH,
    WEAK_MASTERY_THRESHOLD,
    PrerequisiteCycleError,
    PrerequisiteSelfLoopError,
    TargetConceptNotApprovedError,
    TargetConceptNotFoundError,
    plan_learning_path,
)
from app.learning.prerequisites import (
    Concept as DomainConcept,
)
from app.learning.prerequisites import (
    PrerequisiteRelation as DomainPrerequisiteRelation,
)
from app.models import (
    Chunk,
    ConceptCandidate,
    Course,
    CourseIndex,
    CourseIndexStatus,
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    Enrollment,
    EnrollmentStatus,
    EvalCase,
    EvalDataset,
    EvalDatasetStatus,
    EvalDatasetType,
    EvalRun,
    IndexComponentStatus,
    MasteryState,
    RecordStatus,
    RelationCandidate,
    RelationType,
    ReviewStatus,
    User,
    UserRole,
)
from app.retrieval import (
    DENSE_SIMILARITY_FLOOR,
    BGEM3EmbeddingAdapter,
    BGERerankerAdapter,
    DenseCandidate,
    HybridRetrievalConfig,
    HybridRetriever,
    LexicalIndexManager,
    PostgresPgVectorDenseRetriever,
)

THREE_BASELINE_REPORT_SCHEMA_VERSION = (
    "coursepilot.three-baseline-retrieval-report/1.0.0"
)
BASELINE_MODES = ("dense_only", "hybrid_rerank", "kg_personalized")


class EvaluationRunnerError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.details = dict(details or {})


def _require_frozen_at(dataset: EvalDataset) -> datetime:
    """Return the recorded freeze time; FROZEN validation happens upstream."""

    if dataset.frozen_at is None:
        raise EvaluationRunnerError(
            "EVAL_DATASET_NOT_FROZEN",
            "The dataset must be FROZEN with a recorded freeze time",
        )
    return dataset.frozen_at


@dataclass(frozen=True, slots=True)
class EvaluationProviders:
    dense: Any
    lexical: Any
    reranker: Any
    model_versions: Mapping[str, str]


class EvaluationProviderFactory(Protocol):
    def build(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        dialect_name: str,
        course_index: CourseIndex,
    ) -> EvaluationProviders: ...


class _ReadyIndexPgVectorDenseRetriever:
    """Evaluation-only pgvector route for an unpublished READY index.

    The normal runtime adapter intentionally serves ACTIVE indexes only. This
    scoped adapter uses the same BGE query adapter and pgvector distance query,
    while requiring the exact READY course index selected by the evaluator.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding: BGEM3EmbeddingAdapter,
        *,
        course_id: uuid.UUID,
        index_version: int,
        dialect_name: str,
        embedding_dimension: int = 1024,
    ) -> None:
        self.session_factory = session_factory
        self.embedding = embedding
        self.course_id = course_id
        self.index_version = index_version
        self.dialect_name = dialect_name
        self.embedding_dimension = embedding_dimension

    async def search(
        self,
        *,
        course_id: str,
        index_version: str,
        query: str,
        top_k: int = 20,
        filters: Mapping[str, Any] | None = None,
    ) -> list[DenseCandidate]:
        if self.dialect_name != "postgresql":
            raise EvaluationRunnerError(
                "EVAL_DENSE_REQUIRES_POSTGRES",
                "Dense evaluation requires PostgreSQL with pgvector",
            )
        if str(self.course_id) != str(course_id) or str(self.index_version) != str(
            index_version
        ):
            raise PermissionError("dense evaluation scope does not match the index")
        if filters:
            raise ValueError("metadata filters are not supported by dense evaluation")
        if not query.strip():
            raise ValueError("query must not be blank")
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")

        query_vector = [
            float(value) for value in await self.embedding.embed_query(query)
        ]
        if len(query_vector) != self.embedding_dimension or any(
            not math.isfinite(value) for value in query_vector
        ):
            raise ValueError("embedding model returned an invalid query vector")

        published_version = aliased(DocumentVersion)
        latest_selected_version = (
            select(func.max(published_version.version))
            .where(
                published_version.document_id == Document.id,
                published_version.status.in_(
                    (
                        DocumentVersionStatus.READY_FOR_REVIEW,
                        DocumentVersionStatus.PUBLISHED,
                    )
                ),
                published_version.deleted_at.is_(None),
            )
            .correlate(Document)
            .scalar_subquery()
        )
        selected_index_exists = exists(
            select(CourseIndex.id).where(
                CourseIndex.course_id == self.course_id,
                CourseIndex.version == self.index_version,
                CourseIndex.status == CourseIndexStatus.READY,
                CourseIndex.deleted_at.is_(None),
            )
        )
        distance = Chunk.embedding.cosine_distance(query_vector).label(
            "cosine_distance"
        )
        statement = (
            select(Chunk, DocumentVersion, Document, distance)
            .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                selected_index_exists,
                Document.course_id == self.course_id,
                Document.status == DocumentStatus.ACTIVE,
                Document.deleted_at.is_(None),
                DocumentVersion.status.in_(
                    (
                        DocumentVersionStatus.READY_FOR_REVIEW,
                        DocumentVersionStatus.PUBLISHED,
                    )
                ),
                DocumentVersion.deleted_at.is_(None),
                DocumentVersion.version == latest_selected_version,
                Chunk.status == RecordStatus.ACTIVE,
                Chunk.deleted_at.is_(None),
                Chunk.embedding.is_not(None),
            )
            .order_by(distance.asc(), Chunk.id.asc())
            .limit(top_k)
        )
        async with self.session_factory() as session:
            rows = (await session.execute(statement)).all()

        candidates: list[DenseCandidate] = []
        for chunk, version, document, raw_distance in rows:
            similarity = 1.0 - float(raw_distance)
            if not math.isfinite(similarity):
                continue
            candidates.append(
                DenseCandidate(
                    chunk_id=str(chunk.id),
                    score=similarity,
                    content=chunk.content,
                    metadata={
                        "normalized_score": max(
                            0.0,
                            min(
                                1.0,
                                (similarity - DENSE_SIMILARITY_FLOOR)
                                / (1.0 - DENSE_SIMILARITY_FLOOR),
                            ),
                        ),
                        "document": document.logical_name,
                        "document_version": str(version.version),
                        "section": (
                            " / ".join(chunk.section_path)
                            if chunk.section_path
                            else None
                        ),
                        "page": chunk.page,
                    },
                )
            )
        return candidates


class DefaultEvaluationProviderFactory:
    def build(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
        dialect_name: str,
        course_index: CourseIndex,
    ) -> EvaluationProviders:
        embedding = BGEM3EmbeddingAdapter(
            settings.embedding_model,
            allow_download=settings.model_allow_download,
            cache_folder=settings.hf_hub_cache,
        )
        if course_index.status == CourseIndexStatus.ACTIVE:
            dense: Any = PostgresPgVectorDenseRetriever(
                session_factory,
                embedding,
                dialect_name=dialect_name,
            )
        else:
            dense = _ReadyIndexPgVectorDenseRetriever(
                session_factory,
                embedding,
                course_id=course_index.course_id,
                index_version=course_index.version,
                dialect_name=dialect_name,
            )

        lexical = LexicalIndexManager(settings.index_root)
        expected_artifact = lexical.store.artifact_path(
            str(course_index.course_id), str(course_index.version)
        )
        if not course_index.lexical_path:
            raise EvaluationRunnerError(
                "EVAL_LEXICAL_ARTIFACT_MISSING",
                "The selected course index has no lexical artifact path",
            )
        if Path(course_index.lexical_path).resolve() != expected_artifact.resolve():
            raise EvaluationRunnerError(
                "EVAL_LEXICAL_ARTIFACT_MISMATCH",
                "The selected course index points outside its versioned lexical artifact",
                details={
                    "recorded_path": course_index.lexical_path,
                    "expected_path": str(expected_artifact),
                },
            )
        if not expected_artifact.is_file():
            raise EvaluationRunnerError(
                "EVAL_LEXICAL_ARTIFACT_MISSING",
                "The selected course index lexical artifact does not exist",
                details={"path": str(expected_artifact)},
            )

        reranker = BGERerankerAdapter(
            settings.reranker_model,
            allow_download=settings.model_allow_download,
            cache_folder=settings.hf_hub_cache,
        )
        return EvaluationProviders(
            dense=dense,
            lexical=lexical,
            reranker=reranker,
            model_versions={
                "embedding": settings.embedding_model,
                "reranker": settings.reranker_model,
                "lexical": "coursepilot-lightweight-bm25/artifact-v1",
                "graph": "postgres-approved-review-records/v1",
                "personalization": "beta-bernoulli-mastery/v1",
            },
        )


@dataclass(frozen=True, slots=True)
class PersonalizationContext:
    student_id: uuid.UUID
    target_concept_ids: tuple[uuid.UUID, ...]
    concepts: Mapping[uuid.UUID, ConceptCandidate]
    relations: tuple[RelationCandidate, ...]
    masteries: Mapping[uuid.UUID, float]


@dataclass(frozen=True, slots=True)
class ThreeBaselineResult:
    report_path: Path
    report: Mapping[str, Any]
    eval_run_ids: Mapping[str, uuid.UUID]


def _case_error(case: EvalCase, code: str, message: str) -> EvaluationRunnerError:
    return EvaluationRunnerError(
        code,
        message,
        details={"case_key": case.case_key},
    )


def _case_query(case: EvalCase) -> str:
    query = case.input.get("query")
    if not isinstance(query, str) or not query.strip():
        raise _case_error(
            case,
            "EVAL_CASE_QUERY_MISSING",
            "Every evaluation case must provide input.query",
        )
    return query.strip()


def _uuid_list(value: object, *, case: EvalCase, field: str) -> tuple[uuid.UUID, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
    ):
        raise _case_error(
            case,
            "EVAL_PERSONALIZATION_CONTEXT_MISSING",
            f"kg_personalized requires a non-empty input.{field}",
        )
    result: list[uuid.UUID] = []
    for item in value:
        try:
            parsed = uuid.UUID(str(item))
        except (TypeError, ValueError) as exc:
            raise _case_error(
                case,
                "EVAL_PERSONALIZATION_CONTEXT_INVALID",
                f"input.{field} must contain UUIDs",
            ) from exc
        if parsed not in result:
            result.append(parsed)
    return tuple(result)


async def _personalization_context(
    session: AsyncSession,
    *,
    case: EvalCase,
    course_id: uuid.UUID,
    course_index: CourseIndex,
) -> PersonalizationContext:
    raw_student_id = case.input.get("student_id")
    try:
        student_id = uuid.UUID(str(raw_student_id))
    except (TypeError, ValueError) as exc:
        raise _case_error(
            case,
            "EVAL_PERSONALIZATION_CONTEXT_MISSING",
            "kg_personalized requires input.student_id",
        ) from exc
    target_ids = _uuid_list(
        case.input.get("target_concept_ids"),
        case=case,
        field="target_concept_ids",
    )

    enrollment = await session.scalar(
        select(Enrollment.id).where(
            Enrollment.course_id == course_id,
            Enrollment.student_id == student_id,
            Enrollment.status == EnrollmentStatus.ACTIVE,
        )
    )
    if enrollment is None:
        raise _case_error(
            case,
            "EVAL_PERSONALIZATION_STUDENT_NOT_ENROLLED",
            "input.student_id is not actively enrolled in the dataset course",
        )

    concepts = list(
        (
            await session.scalars(
                select(ConceptCandidate).where(
                    ConceptCandidate.course_id == course_id,
                    ConceptCandidate.index_id == course_index.id,
                    ConceptCandidate.status == ReviewStatus.APPROVED,
                    ConceptCandidate.deleted_at.is_(None),
                )
            )
        ).all()
    )
    concept_map = {concept.id: concept for concept in concepts}
    missing_targets = [str(item) for item in target_ids if item not in concept_map]
    if missing_targets:
        raise _case_error(
            case,
            "EVAL_PERSONALIZATION_TARGET_NOT_APPROVED",
            "Every target concept must be APPROVED in the selected index",
        )

    relations = tuple(
        relation
        for relation in (
            await session.scalars(
                select(RelationCandidate).where(
                    RelationCandidate.course_id == course_id,
                    RelationCandidate.status == ReviewStatus.APPROVED,
                    RelationCandidate.deleted_at.is_(None),
                )
            )
        ).all()
        if relation.from_candidate_id in concept_map
        and relation.to_candidate_id in concept_map
    )
    mastery_rows = list(
        (
            await session.scalars(
                select(MasteryState).where(
                    MasteryState.course_id == course_id,
                    MasteryState.student_id == student_id,
                    MasteryState.concept_id.in_(tuple(concept_map)),
                    MasteryState.status == RecordStatus.ACTIVE,
                    MasteryState.deleted_at.is_(None),
                )
            )
        ).all()
    )
    masteries = {row.concept_id: float(row.mastery) for row in mastery_rows}
    missing_mastery = [str(item) for item in target_ids if item not in masteries]
    if missing_mastery:
        raise _case_error(
            case,
            "EVAL_PERSONALIZATION_MASTERY_MISSING",
            "Every target concept needs an actual active MasteryState",
        )
    return PersonalizationContext(
        student_id=student_id,
        target_concept_ids=target_ids,
        concepts=concept_map,
        relations=relations,
        masteries=masteries,
    )


async def _authoritative_chunk_ids(
    session: AsyncSession,
    *,
    course_index: CourseIndex,
    candidate_ids: Sequence[str],
) -> list[str]:
    ordered: list[uuid.UUID] = []
    for raw_id in candidate_ids:
        try:
            chunk_id = uuid.UUID(str(raw_id))
        except (TypeError, ValueError) as exc:
            raise EvaluationRunnerError(
                "EVAL_RETRIEVER_RETURNED_INVALID_CHUNK",
                "A retrieval provider returned a non-UUID chunk ID",
                details={"chunk_id": str(raw_id)},
            ) from exc
        if chunk_id not in ordered:
            ordered.append(chunk_id)
    if not ordered:
        return []

    allowed_version_statuses = (
        (DocumentVersionStatus.PUBLISHED,)
        if course_index.status == CourseIndexStatus.ACTIVE
        else (
            DocumentVersionStatus.READY_FOR_REVIEW,
            DocumentVersionStatus.PUBLISHED,
        )
    )
    published_version = aliased(DocumentVersion)
    latest_selected_version = (
        select(func.max(published_version.version))
        .where(
            published_version.document_id == Document.id,
            published_version.status.in_(allowed_version_statuses),
            published_version.deleted_at.is_(None),
        )
        .correlate(Document)
        .scalar_subquery()
    )
    rows = set(
        (
            await session.scalars(
                select(Chunk.id)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Chunk.id.in_(ordered),
                    Chunk.status == RecordStatus.ACTIVE,
                    Chunk.deleted_at.is_(None),
                    DocumentVersion.status.in_(allowed_version_statuses),
                    DocumentVersion.deleted_at.is_(None),
                    DocumentVersion.version == latest_selected_version,
                    Document.course_id == course_index.course_id,
                    Document.status == DocumentStatus.ACTIVE,
                    Document.deleted_at.is_(None),
                )
            )
        ).all()
    )
    out_of_scope = [str(item) for item in ordered if item not in rows]
    if out_of_scope:
        raise EvaluationRunnerError(
            "EVAL_RETRIEVER_SCOPE_VIOLATION",
            "A retrieval provider returned chunks outside the selected course corpus",
            details={"chunk_ids": out_of_scope},
        )
    return [str(item) for item in ordered]


def _edge_factor(relation: RelationCandidate, current: uuid.UUID) -> float:
    if relation.type == RelationType.PREREQUISITE_OF:
        return 0.95 if relation.to_candidate_id == current else 0.60
    if relation.type == RelationType.PART_OF:
        return 0.75
    if relation.type == RelationType.RELATED_TO:
        return 0.65
    return 0.50


async def _personalize(
    session: AsyncSession,
    *,
    course_index: CourseIndex,
    base_ranking: Sequence[str],
    context: PersonalizationContext,
    final_top_k: int,
    graph_depth: int,
    kg_weight: float,
) -> tuple[list[str], dict[str, Any]]:
    influence = {concept_id: 1.0 for concept_id in context.target_concept_ids}
    frontier: deque[tuple[uuid.UUID, int]] = deque(
        (concept_id, 0) for concept_id in context.target_concept_ids
    )
    used_relations: set[uuid.UUID] = set()
    while frontier:
        current, depth = frontier.popleft()
        if depth >= graph_depth:
            continue
        for relation in context.relations:
            if relation.from_candidate_id == current:
                neighbor = relation.to_candidate_id
            elif relation.to_candidate_id == current:
                neighbor = relation.from_candidate_id
            else:
                continue
            used_relations.add(relation.id)
            propagated = influence[current] * _edge_factor(relation, current) * 0.8
            if propagated > influence.get(neighbor, 0.0):
                influence[neighbor] = propagated
                frontier.append((neighbor, depth + 1))

    chunk_boosts: dict[str, float] = {}
    concept_scores: dict[uuid.UUID, float] = {}
    for concept_id, graph_score in influence.items():
        mastery = context.masteries.get(concept_id)
        if mastery is None:
            continue
        score = graph_score * (1.0 - mastery)
        concept_scores[concept_id] = score
        chunk_id = str(context.concepts[concept_id].source_chunk_id)
        chunk_boosts[chunk_id] = max(chunk_boosts.get(chunk_id, 0.0), score)
    for relation in context.relations:
        if relation.id not in used_relations:
            continue
        endpoint_score = max(
            concept_scores.get(relation.from_candidate_id, 0.0),
            concept_scores.get(relation.to_candidate_id, 0.0),
        )
        if endpoint_score > 0:
            chunk_id = str(relation.source_chunk_id)
            chunk_boosts[chunk_id] = max(
                chunk_boosts.get(chunk_id, 0.0), endpoint_score * 0.8
            )

    augmented = list(base_ranking)
    augmented.extend(item for item in chunk_boosts if item not in augmented)
    augmented = await _authoritative_chunk_ids(
        session,
        course_index=course_index,
        candidate_ids=augmented,
    )
    base_rank = {chunk_id: rank for rank, chunk_id in enumerate(base_ranking, start=1)}
    scored = sorted(
        augmented,
        key=lambda chunk_id: (
            -(
                (1.0 / base_rank[chunk_id] if chunk_id in base_rank else 0.0)
                + kg_weight * chunk_boosts.get(chunk_id, 0.0)
            ),
            base_rank.get(chunk_id, len(base_rank) + 1),
            chunk_id,
        ),
    )[:final_top_k]
    trace = {
        "student_id": str(context.student_id),
        "target_concept_ids": [str(item) for item in context.target_concept_ids],
        "approved_concept_ids_used": [
            str(item) for item in sorted(concept_scores, key=str)
        ],
        "approved_relation_ids_used": [
            str(item) for item in sorted(used_relations, key=str)
        ],
        "mastery_states_used": {
            str(item): context.masteries[item]
            for item in sorted(concept_scores, key=str)
        },
        "chunk_boosts": dict(sorted(chunk_boosts.items())),
        "base_ranked_chunk_ids": list(base_ranking),
        "ranked_chunk_ids": scored,
    }
    return scored, trace


def _validate_trace(mode: str, trace: Mapping[str, Any]) -> None:
    stages = trace["stages"]
    if stages["dense"]["status"] != "success":
        raise EvaluationRunnerError(
            "EVAL_DENSE_ROUTE_FAILED",
            f"{mode} did not execute a successful dense route",
            details={"trace": trace},
        )
    if mode != "dense_only" and stages["lexical"]["status"] != "success":
        raise EvaluationRunnerError(
            "EVAL_LEXICAL_ROUTE_FAILED",
            f"{mode} did not execute a successful lexical route",
            details={"trace": trace},
        )
    if mode != "dense_only" and stages["reranker"]["status"] not in {
        "success",
        "skipped_empty",
    }:
        raise EvaluationRunnerError(
            "EVAL_RERANKER_FAILED",
            f"{mode} did not execute its configured reranker",
            details={"trace": trace},
        )


def _hardware_provenance() -> dict[str, Any]:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
        "python": platform.python_version(),
        "logical_cpu_count": os.cpu_count(),
        "cuda_visible_devices": os.getenv("CUDA_VISIBLE_DEVICES"),
    }


def _git_provenance() -> tuple[str, bool]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise EvaluationRunnerError(
            "EVAL_GIT_PROVENANCE_UNAVAILABLE",
            "Could not determine the Git revision for this evaluation",
        ) from exc
    return commit, dirty


def _report_version(
    dataset: EvalDataset, index_version: int, generated_at: datetime
) -> str:
    timestamp = generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"retrieval-ds-v{dataset.version}-index-v{index_version}-{timestamp}"


def _write_report(
    output_dir: str | Path, report_version: str, report: Mapping[str, Any]
) -> Path:
    if (
        not report_version
        or report_version in {".", ".."}
        or "/" in report_version
        or "\\" in report_version
        or "\x00" in report_version
    ):
        raise EvaluationRunnerError(
            "EVAL_REPORT_VERSION_INVALID",
            "report_version must be a safe non-empty filename component",
        )
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{report_version}.json"
    try:
        with target.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(
                report,
                stream,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
    except FileExistsError as exc:
        raise EvaluationRunnerError(
            "EVAL_REPORT_ALREADY_EXISTS",
            "The versioned evaluation report already exists",
            details={"path": str(target)},
        ) from exc
    return target


async def run_three_baselines(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    dataset_id: uuid.UUID,
    index_version: int,
    output_dir: str | Path,
    provider_factory: EvaluationProviderFactory | None = None,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
    report_version: str | None = None,
    prompt_version: str = "no-generation/retrieval-eval-v1",
    generated_at: datetime | None = None,
    hardware: Mapping[str, Any] | None = None,
    route_top_k: int = 20,
    fusion_top_k: int = 20,
    final_top_k: int = 10,
    rrf_k: int = 60,
    reranker_timeout_seconds: float = 15.0,
    graph_depth: int = 2,
    kg_weight: float = 1.0,
) -> ThreeBaselineResult:
    if index_version < 1:
        raise ValueError("index_version must be positive")
    if graph_depth < 0 or graph_depth > 4:
        raise ValueError("graph_depth must be between 0 and 4")
    if not math.isfinite(kg_weight) or kg_weight < 0:
        raise ValueError("kg_weight must be finite and non-negative")
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    if git_commit is None:
        git_commit, discovered_dirty = _git_provenance()
        git_dirty = discovered_dirty if git_dirty is None else git_dirty
    if not git_commit.strip():
        raise ValueError("git_commit must not be blank")
    hardware_details = dict(hardware or _hardware_provenance())
    retrieval_parameters = {
        "route_top_k": route_top_k,
        "fusion_top_k": fusion_top_k,
        "final_top_k": final_top_k,
        "rrf_k": rrf_k,
        "reranker_timeout_seconds": reranker_timeout_seconds,
        "graph_depth": graph_depth,
        "kg_weight": kg_weight,
    }
    retrieval_config = HybridRetrievalConfig(
        route_top_k=route_top_k,
        fusion_top_k=fusion_top_k,
        final_top_k=final_top_k,
        rrf_k=rrf_k,
        reranker_timeout_seconds=reranker_timeout_seconds,
    )

    async with session_factory() as session:
        dataset = await session.scalar(
            select(EvalDataset).where(
                EvalDataset.id == dataset_id,
                EvalDataset.deleted_at.is_(None),
            )
        )
        if dataset is None:
            raise EvaluationRunnerError(
                "EVAL_DATASET_NOT_FOUND", "Evaluation dataset not found"
            )
        if dataset.type != EvalDatasetType.RETRIEVAL:
            raise EvaluationRunnerError(
                "EVAL_DATASET_TYPE_INVALID",
                "The three-baseline runner only accepts RETRIEVAL datasets",
            )
        if dataset.status != EvalDatasetStatus.FROZEN or dataset.frozen_at is None:
            raise EvaluationRunnerError(
                "EVAL_DATASET_NOT_FROZEN",
                "The three-baseline runner requires a FROZEN dataset",
            )
        course = await session.scalar(
            select(Course).where(Course.id == dataset.course_id)
        )
        teacher: User | None = None
        if course is not None:
            teacher = await session.scalar(
                select(User).where(User.id == course.owner_id)
            )
        if course is None or teacher is None or teacher.role != UserRole.TEACHER:
            raise EvaluationRunnerError(
                "EVAL_COURSE_OWNER_INVALID",
                "The dataset course has no valid teacher owner",
            )
        course_index = await session.scalar(
            select(CourseIndex).where(
                CourseIndex.course_id == dataset.course_id,
                CourseIndex.version == index_version,
                CourseIndex.deleted_at.is_(None),
            )
        )
        if course_index is None:
            raise EvaluationRunnerError(
                "EVAL_INDEX_NOT_FOUND", "The selected course index was not found"
            )
        if course_index.status not in {
            CourseIndexStatus.READY,
            CourseIndexStatus.ACTIVE,
        }:
            raise EvaluationRunnerError(
                "EVAL_INDEX_NOT_READY",
                "The selected course index must be READY or ACTIVE",
            )
        if course_index.dense_status != IndexComponentStatus.READY:
            raise EvaluationRunnerError(
                "EVAL_DENSE_INDEX_NOT_READY",
                "All three baselines require a READY dense index",
            )
        if course_index.lexical_status != IndexComponentStatus.READY:
            raise EvaluationRunnerError(
                "EVAL_LEXICAL_INDEX_NOT_READY",
                "Hybrid baselines require a READY lexical index",
            )
        cases = await _active_cases(session, dataset.id)
        if not cases:
            raise EvaluationRunnerError(
                "EVAL_DATASET_EMPTY", "The frozen dataset has no active cases"
            )
        for case in cases:
            _case_query(case)

        # Validate every personalized case before persisting any baseline run.
        personalization = {
            case.case_key: await _personalization_context(
                session,
                case=case,
                course_id=dataset.course_id,
                course_index=course_index,
            )
            for case in cases
        }
        factory = provider_factory or DefaultEvaluationProviderFactory()
        engine = session.get_bind()
        providers = factory.build(
            session_factory=session_factory,
            settings=settings,
            dialect_name=engine.dialect.name,
            course_index=course_index,
        )
        if not providers.model_versions:
            raise EvaluationRunnerError(
                "EVAL_MODEL_PROVENANCE_MISSING",
                "The provider factory must report model versions",
            )

        snapshot = _domain_snapshot(
            dataset, cases, frozen_at=_require_frozen_at(dataset)
        )
        selected_report_version = report_version or _report_version(
            dataset, index_version, generated
        )
        baselines: list[dict[str, Any]] = []
        run_ids: dict[str, uuid.UUID] = {}
        hybrid_outputs: dict[str, dict[str, Any]] = {}
        for mode in BASELINE_MODES:
            retriever = HybridRetriever(
                dense=providers.dense,
                lexical=providers.lexical if mode != "dense_only" else None,
                reranker=providers.reranker if mode != "dense_only" else None,
                config=retrieval_config,
            )
            case_results: dict[str, Any] = {}
            started = time.perf_counter()
            for case in cases:
                query = _case_query(case)
                if mode == "kg_personalized":
                    cached = hybrid_outputs[case.case_key]
                    trace = copy.deepcopy(cached["retrieval_trace"])
                    ranked = list(cached["ranked_chunk_ids"])
                    _validate_trace(mode, trace)
                else:
                    result = await retriever.retrieve(
                        course_id=str(dataset.course_id),
                        index_version=str(index_version),
                        query=query,
                        top_k=final_top_k,
                        trace_id=str(uuid.uuid4()),
                    )
                    trace = result.trace.to_dict()
                    _validate_trace(mode, trace)
                    ranked = await _authoritative_chunk_ids(
                        session,
                        course_index=course_index,
                        candidate_ids=[
                            candidate.chunk_id for candidate in result.candidates
                        ],
                    )
                output: dict[str, Any] = {
                    "ranked_chunk_ids": ranked,
                    "retrieval_trace": trace,
                }
                if mode == "hybrid_rerank":
                    hybrid_outputs[case.case_key] = copy.deepcopy(output)
                if mode == "kg_personalized":
                    ranked, personalization_trace = await _personalize(
                        session,
                        course_index=course_index,
                        base_ranking=ranked,
                        context=personalization[case.case_key],
                        final_top_k=final_top_k,
                        graph_depth=graph_depth,
                        kg_weight=kg_weight,
                    )
                    output["ranked_chunk_ids"] = ranked
                    output["personalization_trace"] = personalization_trace
                    output["base_retrieval_reused"] = True
                case_results[case.case_key] = output

            execution_duration_ms = (time.perf_counter() - started) * 1000
            reused_retrieval_ms = (
                sum(
                    float(
                        item["retrieval_trace"].get("total_latency_ms", 0.0)
                        if isinstance(item.get("retrieval_trace"), dict)
                        else 0.0
                    )
                    for item in hybrid_outputs.values()
                )
                if mode == "kg_personalized"
                else 0.0
            )
            duration_ms = execution_duration_ms + reused_retrieval_ms
            raw_config = {
                "git_commit": git_commit,
                "dataset_version": dataset.version,
                "index_version": index_version,
                "model_versions": dict(providers.model_versions),
                "prompt_version": prompt_version,
                "retrieval_parameters": retrieval_parameters,
                "hardware": hardware_details,
                "experiment": mode,
                "report_version": selected_report_version,
                "git_dirty": git_dirty,
                "duration_ms": round(duration_ms, 3),
                "execution_duration_ms": round(execution_duration_ms, 3),
                "base_retrieval_reused": mode == "kg_personalized",
            }
            run = await create_run(
                session,
                dataset_id=dataset.id,
                teacher=teacher,
                raw_config=raw_config,
                case_results=case_results,
            )
            run_ids[mode] = run.id
            baselines.append(
                {
                    "mode": mode,
                    "eval_run_id": str(run.id),
                    "trace_id": str(run.trace_id),
                    "duration_ms": round(duration_ms, 3),
                    "execution_duration_ms": round(execution_duration_ms, 3),
                    "base_retrieval_reused": mode == "kg_personalized",
                    "metrics": run.metrics,
                    "case_results": case_results,
                }
            )

        report: dict[str, Any] = {
            "schema_version": THREE_BASELINE_REPORT_SCHEMA_VERSION,
            "report_version": selected_report_version,
            "generated_at": generated.isoformat().replace("+00:00", "Z"),
            "dataset": {
                "id": str(dataset.id),
                "type": dataset.type.value,
                "version": dataset.version,
                "frozen_at": _require_frozen_at(dataset)
                .isoformat()
                .replace("+00:00", "Z"),
                "content_sha256": snapshot.content_sha256,
                "case_count": len(cases),
            },
            "course_index": {
                "id": str(course_index.id),
                "course_id": str(course_index.course_id),
                "version": course_index.version,
                "status": course_index.status.value,
                "dense_status": course_index.dense_status.value,
                "lexical_status": course_index.lexical_status.value,
            },
            "provenance": {
                "git_commit": git_commit,
                "git_dirty": git_dirty,
                "model_versions": dict(providers.model_versions),
                "prompt_version": prompt_version,
                "retrieval_parameters": retrieval_parameters,
                "hardware": hardware_details,
            },
            "baselines": baselines,
        }
        report_path = _write_report(output_dir, selected_report_version, report)
        return ThreeBaselineResult(report_path, report, run_ids)


CASE_EVALUATION_REPORT_SCHEMA_VERSION = "coursepilot.case-evaluation-report/2.0.0"
DETERMINISTIC_ROUTER_VERSION = "coursepilot-deterministic-intent-rules/v1"
PATH_PLANNER_VERSION = "coursepilot-prerequisite-topological-planner/v1"
LEXICAL_GROUNDING_VERSION = "coursepilot-lexical-claim-support/v1"
NO_GENERATION_QA_VERSION = "no-generation/evidence-only-summary/v1"


@dataclass(frozen=True, slots=True)
class CaseEvaluationResult:
    report_path: Path
    report: Mapping[str, Any]
    eval_run_id: uuid.UUID


def _required_uuid(case: EvalCase, raw: object, code: str, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise _case_error(
            case,
            code,
            f"input.{field} must be a UUID",
        ) from exc


def _optional_int(
    case: EvalCase, raw: object, field: str, *, default: int, minimum: int, maximum: int
) -> int:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _case_error(
            case,
            "EVAL_CASE_PARAMETER_INVALID",
            f"input.{field} must be an integer",
        )
    if not minimum <= raw <= maximum:
        raise _case_error(
            case,
            "EVAL_CASE_PARAMETER_INVALID",
            f"input.{field} must be between {minimum} and {maximum}",
        )
    return raw


def _optional_float(
    case: EvalCase,
    raw: object,
    field: str,
    *,
    default: float,
    minimum: float = 0.0,
    maximum: float = 1.0,
) -> float:
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise _case_error(
            case,
            "EVAL_CASE_PARAMETER_INVALID",
            f"input.{field} must be a number",
        )
    value = float(raw)
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise _case_error(
            case,
            "EVAL_CASE_PARAMETER_INVALID",
            f"input.{field} must be between {minimum} and {maximum}",
        )
    return value


def _canonical_chunk_id_set(case: EvalCase, raw: object, field: str) -> frozenset[str]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise _case_error(
            case,
            "EVAL_CASE_PARAMETER_INVALID",
            f"{field} must be an array of chunk ids",
        )
    canonical: set[str] = set()
    for item in raw:
        try:
            canonical.add(str(uuid.UUID(str(item))))
        except (TypeError, ValueError) as exc:
            raise _case_error(
                case,
                "EVAL_CASE_PARAMETER_INVALID",
                f"{field} must contain UUID chunk ids",
            ) from exc
    return frozenset(canonical)


async def _case_evaluation_context(
    session: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    index_version: int,
    expected_type: EvalDatasetType,
    require_retrieval_components: bool,
) -> tuple[EvalDataset, CourseIndex, list[EvalCase], User]:
    dataset = await session.scalar(
        select(EvalDataset).where(
            EvalDataset.id == dataset_id,
            EvalDataset.deleted_at.is_(None),
        )
    )
    if dataset is None:
        raise EvaluationRunnerError(
            "EVAL_DATASET_NOT_FOUND", "Evaluation dataset not found"
        )
    if dataset.type != expected_type:
        raise EvaluationRunnerError(
            "EVAL_DATASET_TYPE_INVALID",
            f"This runner only accepts {expected_type.value} datasets",
        )
    if dataset.status != EvalDatasetStatus.FROZEN or dataset.frozen_at is None:
        raise EvaluationRunnerError(
            "EVAL_DATASET_NOT_FROZEN", "The case runner requires a FROZEN dataset"
        )
    course = await session.scalar(select(Course).where(Course.id == dataset.course_id))
    teacher: User | None = None
    if course is not None:
        teacher = await session.scalar(select(User).where(User.id == course.owner_id))
    if course is None or teacher is None or teacher.role != UserRole.TEACHER:
        raise EvaluationRunnerError(
            "EVAL_COURSE_OWNER_INVALID",
            "The dataset course has no valid teacher owner",
        )
    course_index = await session.scalar(
        select(CourseIndex).where(
            CourseIndex.course_id == dataset.course_id,
            CourseIndex.version == index_version,
            CourseIndex.deleted_at.is_(None),
        )
    )
    if course_index is None:
        raise EvaluationRunnerError(
            "EVAL_INDEX_NOT_FOUND", "The selected course index was not found"
        )
    if course_index.status not in {
        CourseIndexStatus.READY,
        CourseIndexStatus.ACTIVE,
    }:
        raise EvaluationRunnerError(
            "EVAL_INDEX_NOT_READY",
            "The selected course index must be READY or ACTIVE",
        )
    if require_retrieval_components:
        if course_index.dense_status != IndexComponentStatus.READY:
            raise EvaluationRunnerError(
                "EVAL_DENSE_INDEX_NOT_READY",
                "End-to-end QA evaluation requires a READY dense index",
            )
        if course_index.lexical_status != IndexComponentStatus.READY:
            raise EvaluationRunnerError(
                "EVAL_LEXICAL_INDEX_NOT_READY",
                "End-to-end QA evaluation requires a READY lexical index",
            )
    cases = await _active_cases(session, dataset.id)
    if not cases:
        raise EvaluationRunnerError(
            "EVAL_DATASET_EMPTY", "The frozen dataset has no active cases"
        )
    return dataset, course_index, cases, teacher


def _case_report_payload(
    *,
    dataset: EvalDataset,
    course_index: CourseIndex,
    snapshot: Any,
    selected_report_version: str,
    generated: datetime,
    git_commit: str,
    git_dirty: bool,
    model_versions: Mapping[str, str],
    prompt_version: str,
    runner_parameters: Mapping[str, Any],
    hardware_details: Mapping[str, Any],
    run: EvalRun,
    case_results: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": CASE_EVALUATION_REPORT_SCHEMA_VERSION,
        "report_version": selected_report_version,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "dataset": {
            "id": str(dataset.id),
            "type": dataset.type.value,
            "version": dataset.version,
            "frozen_at": _require_frozen_at(dataset).isoformat().replace("+00:00", "Z"),
            "content_sha256": snapshot.content_sha256,
            "case_count": len(case_results),
        },
        "course_index": {
            "id": str(course_index.id),
            "course_id": str(course_index.course_id),
            "version": course_index.version,
            "status": course_index.status.value,
            "dense_status": course_index.dense_status.value,
            "lexical_status": course_index.lexical_status.value,
        },
        "provenance": {
            "git_commit": git_commit,
            "git_dirty": git_dirty,
            "model_versions": dict(model_versions),
            "prompt_version": prompt_version,
            "retrieval_parameters": dict(runner_parameters),
            "hardware": dict(hardware_details),
        },
        "eval_run_id": str(run.id),
        "metrics": run.metrics,
        "case_results": dict(case_results),
    }


def _resolve_git_provenance(
    git_commit: str | None, git_dirty: bool | None
) -> tuple[str, bool]:
    if git_commit is None:
        commit, discovered_dirty = _git_provenance()
        return commit, discovered_dirty if git_dirty is None else git_dirty
    if not git_commit.strip():
        raise ValueError("git_commit must not be blank")
    return git_commit, bool(git_dirty)


async def _persist_case_run(
    session: AsyncSession,
    *,
    dataset: EvalDataset,
    teacher: User,
    index_version: int,
    model_versions: Mapping[str, str],
    prompt_version: str,
    runner_parameters: Mapping[str, Any],
    hardware_details: Mapping[str, Any],
    git_commit: str,
    git_dirty: bool,
    selected_report_version: str,
    duration_ms: float,
    case_results: Mapping[str, Any],
) -> EvalRun:
    raw_config = {
        "git_commit": git_commit,
        "dataset_version": dataset.version,
        "index_version": index_version,
        "model_versions": dict(model_versions),
        "prompt_version": prompt_version,
        "retrieval_parameters": dict(runner_parameters),
        "hardware": dict(hardware_details),
        "experiment": dataset.type.value,
        "report_version": selected_report_version,
        "git_dirty": git_dirty,
        "duration_ms": round(duration_ms, 3),
    }
    return await create_run(
        session,
        dataset_id=dataset.id,
        teacher=teacher,
        raw_config=raw_config,
        case_results=case_results,
    )


def _optional_intent(case: EvalCase, raw: object) -> Intent | None:
    if raw is None:
        return None
    try:
        return Intent(str(raw).strip())
    except ValueError as exc:
        raise _case_error(
            case,
            "EVAL_ROUTING_REQUESTED_INTENT_INVALID",
            "input.requested_intent must be a known intent",
        ) from exc


def _optional_target_concepts(case: EvalCase, raw: object) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise _case_error(
            case,
            "EVAL_ROUTING_TARGETS_INVALID",
            "input.target_concepts must be an array of concept names",
        )
    return [str(item).strip() for item in raw if str(item).strip()]


async def run_intent_routing_evaluation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    dataset_id: uuid.UUID,
    index_version: int,
    output_dir: str | Path,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
    report_version: str | None = None,
    prompt_version: str = "no-generation/intent-routing-eval-v1",
    generated_at: datetime | None = None,
    hardware: Mapping[str, Any] | None = None,
) -> CaseEvaluationResult:
    """Measure the deterministic intent router against a frozen routing dataset.

    Each case output records the ``predicted_intent`` the router selects for the
    frozen query; the metric layer compares it with the labeled intent.
    """

    if index_version < 1:
        raise ValueError("index_version must be positive")
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    commit, dirty = _resolve_git_provenance(git_commit, git_dirty)
    hardware_details = dict(hardware or _hardware_provenance())
    runner_parameters = {
        "router_model": DETERMINISTIC_ROUTER_VERSION,
        "llm_classifier": "not-wired",
    }
    model_versions = {"router": DETERMINISTIC_ROUTER_VERSION}

    async with session_factory() as session:
        dataset, course_index, cases, teacher = await _case_evaluation_context(
            session,
            dataset_id=dataset_id,
            index_version=index_version,
            expected_type=EvalDatasetType.INTENT_ROUTING,
            require_retrieval_components=False,
        )
        router = ValidatedIntentRouter()
        case_results: dict[str, Any] = {}
        started = time.perf_counter()
        for case in cases:
            query = _case_query(case)
            try:
                request = AgentRequest(
                    course_id=str(dataset.course_id),
                    query=query,
                    requested_intent=_optional_intent(
                        case, case.input.get("requested_intent")
                    ),
                    target_concepts=_optional_target_concepts(
                        case, case.input.get("target_concepts")
                    ),
                    active_index_version=str(index_version),
                )
            except ValidationError as exc:
                raise _case_error(
                    case,
                    "EVAL_CASE_INPUT_INVALID",
                    "The routing case input cannot build an AgentRequest",
                ) from exc
            route = await router.route(request)
            case_results[case.case_key] = {
                "predicted_intent": route.intent.value,
                "needs_retrieval": route.needs_retrieval,
                "route_reason": route.reason,
            }
        duration_ms = (time.perf_counter() - started) * 1000

        snapshot = _domain_snapshot(
            dataset, cases, frozen_at=_require_frozen_at(dataset)
        )
        selected_report_version = report_version or _report_version(
            dataset, index_version, generated
        )
        run = await _persist_case_run(
            session,
            dataset=dataset,
            teacher=teacher,
            index_version=index_version,
            model_versions=model_versions,
            prompt_version=prompt_version,
            runner_parameters=runner_parameters,
            hardware_details=hardware_details,
            git_commit=commit,
            git_dirty=dirty,
            selected_report_version=selected_report_version,
            duration_ms=duration_ms,
            case_results=case_results,
        )
        report = _case_report_payload(
            dataset=dataset,
            course_index=course_index,
            snapshot=snapshot,
            selected_report_version=selected_report_version,
            generated=generated,
            git_commit=commit,
            git_dirty=dirty,
            model_versions=model_versions,
            prompt_version=prompt_version,
            runner_parameters=runner_parameters,
            hardware_details=hardware_details,
            run=run,
            case_results=case_results,
        )
        report_path = _write_report(output_dir, selected_report_version, report)
        return CaseEvaluationResult(report_path, report, run.id)


async def run_learning_path_evaluation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    dataset_id: uuid.UUID,
    index_version: int,
    output_dir: str | Path,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
    report_version: str | None = None,
    prompt_version: str = "no-generation/learning-path-eval-v1",
    generated_at: datetime | None = None,
    hardware: Mapping[str, Any] | None = None,
) -> CaseEvaluationResult:
    """Measure the approved-graph learning-path planner against frozen cases.

    Each case supplies ``input.student_id`` and ``input.target_concept_id``; the
    runner plans a path from the concepts and prerequisite relations approved in
    the selected index and the student's actual mastery states.
    """

    if index_version < 1:
        raise ValueError("index_version must be positive")
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    commit, dirty = _resolve_git_provenance(git_commit, git_dirty)
    hardware_details = dict(hardware or _hardware_provenance())
    runner_parameters = {
        "path_planner": PATH_PLANNER_VERSION,
        "mastery_model": "beta-bernoulli-mastery/v1",
        "graph_source": "postgres-approved-review-records/v1",
    }
    model_versions = {
        "path_planner": PATH_PLANNER_VERSION,
        "mastery": "beta-bernoulli-mastery/v1",
        "graph": "postgres-approved-review-records/v1",
    }

    async with session_factory() as session:
        dataset, course_index, cases, teacher = await _case_evaluation_context(
            session,
            dataset_id=dataset_id,
            index_version=index_version,
            expected_type=EvalDatasetType.LEARNING_PATH,
            require_retrieval_components=False,
        )
        concepts = list(
            (
                await session.scalars(
                    select(ConceptCandidate).where(
                        ConceptCandidate.course_id == dataset.course_id,
                        ConceptCandidate.index_id == course_index.id,
                        ConceptCandidate.status == ReviewStatus.APPROVED,
                        ConceptCandidate.deleted_at.is_(None),
                    )
                )
            ).all()
        )
        concept_map = {concept.id: concept for concept in concepts}
        relations = tuple(
            relation
            for relation in (
                await session.scalars(
                    select(RelationCandidate).where(
                        RelationCandidate.course_id == dataset.course_id,
                        RelationCandidate.status == ReviewStatus.APPROVED,
                        RelationCandidate.deleted_at.is_(None),
                    )
                )
            ).all()
            if relation.from_candidate_id in concept_map
            and relation.to_candidate_id in concept_map
        )
        domain_concepts = [
            DomainConcept(concept_id=concept.id, status="APPROVED")
            for concept in concepts
        ]
        domain_relations = [
            DomainPrerequisiteRelation(
                prerequisite_id=relation.from_candidate_id,
                concept_id=relation.to_candidate_id,
                status="APPROVED",
                relation_type=relation.type.value,
            )
            for relation in relations
        ]

        case_results: dict[str, Any] = {}
        started = time.perf_counter()
        for case in cases:
            student_id = _required_uuid(
                case,
                case.input.get("student_id"),
                "EVAL_PATH_STUDENT_MISSING",
                "student_id",
            )
            target_id = _required_uuid(
                case,
                case.input.get("target_concept_id"),
                "EVAL_PATH_TARGET_MISSING",
                "target_concept_id",
            )
            max_depth = _optional_int(
                case,
                case.input.get("max_depth"),
                "max_depth",
                default=MAX_PREREQUISITE_DEPTH,
                minimum=0,
                maximum=MAX_PREREQUISITE_DEPTH,
            )
            weak_threshold = _optional_float(
                case,
                case.input.get("weak_threshold"),
                "weak_threshold",
                default=WEAK_MASTERY_THRESHOLD,
            )
            enrollment = await session.scalar(
                select(Enrollment.id).where(
                    Enrollment.course_id == dataset.course_id,
                    Enrollment.student_id == student_id,
                    Enrollment.status == EnrollmentStatus.ACTIVE,
                )
            )
            if enrollment is None:
                raise _case_error(
                    case,
                    "EVAL_PATH_STUDENT_NOT_ENROLLED",
                    "input.student_id is not actively enrolled in the dataset course",
                )
            if target_id not in concept_map:
                known = await session.scalar(
                    select(ConceptCandidate.id).where(
                        ConceptCandidate.id == target_id,
                        ConceptCandidate.course_id == dataset.course_id,
                        ConceptCandidate.deleted_at.is_(None),
                    )
                )
                if known is None:
                    raise _case_error(
                        case,
                        "EVAL_PATH_TARGET_NOT_FOUND",
                        "input.target_concept_id does not exist in the dataset course",
                    )
                raise _case_error(
                    case,
                    "EVAL_PATH_TARGET_NOT_APPROVED",
                    "Every target concept must be APPROVED in the selected index",
                )
            mastery_rows = list(
                (
                    await session.scalars(
                        select(MasteryState).where(
                            MasteryState.course_id == dataset.course_id,
                            MasteryState.student_id == student_id,
                            MasteryState.status == RecordStatus.ACTIVE,
                            MasteryState.deleted_at.is_(None),
                        )
                    )
                ).all()
            )
            mastery_by_concept: dict[Hashable, float] = {
                row.concept_id: float(row.mastery) for row in mastery_rows
            }
            try:
                path = plan_learning_path(
                    target_id,
                    domain_concepts,
                    domain_relations,
                    mastery_by_concept,
                    max_depth=max_depth,
                    weak_threshold=weak_threshold,
                )
            except TargetConceptNotFoundError as exc:
                raise _case_error(
                    case,
                    "EVAL_PATH_TARGET_NOT_FOUND",
                    "input.target_concept_id does not exist in the dataset course",
                ) from exc
            except TargetConceptNotApprovedError as exc:
                raise _case_error(
                    case,
                    "EVAL_PATH_TARGET_NOT_APPROVED",
                    "Every target concept must be APPROVED in the selected index",
                ) from exc
            except (PrerequisiteCycleError, PrerequisiteSelfLoopError) as exc:
                raise _case_error(
                    case,
                    "EVAL_PATH_PREREQUISITE_CYCLE",
                    "The approved prerequisite graph contains a cycle",
                ) from exc
            case_results[case.case_key] = {
                "predicted_concept_ids": [str(item) for item in path.concept_ids],
                "target_concept_id": str(target_id),
                "student_id": str(student_id),
                "max_depth": max_depth,
                "weak_threshold": weak_threshold,
                "steps": [
                    {
                        "concept_id": str(step.concept_id),
                        "mastery": step.mastery,
                        "is_weak": step.is_weak,
                        "depth": step.depth,
                        "reason": step.reason,
                    }
                    for step in path.steps
                ],
            }
        duration_ms = (time.perf_counter() - started) * 1000

        snapshot = _domain_snapshot(
            dataset, cases, frozen_at=_require_frozen_at(dataset)
        )
        selected_report_version = report_version or _report_version(
            dataset, index_version, generated
        )
        run = await _persist_case_run(
            session,
            dataset=dataset,
            teacher=teacher,
            index_version=index_version,
            model_versions=model_versions,
            prompt_version=prompt_version,
            runner_parameters=runner_parameters,
            hardware_details=hardware_details,
            git_commit=commit,
            git_dirty=dirty,
            selected_report_version=selected_report_version,
            duration_ms=duration_ms,
            case_results=case_results,
        )
        report = _case_report_payload(
            dataset=dataset,
            course_index=course_index,
            snapshot=snapshot,
            selected_report_version=selected_report_version,
            generated=generated,
            git_commit=commit,
            git_dirty=dirty,
            model_versions=model_versions,
            prompt_version=prompt_version,
            runner_parameters=runner_parameters,
            hardware_details=hardware_details,
            run=run,
            case_results=case_results,
        )
        report_path = _write_report(output_dir, selected_report_version, report)
        return CaseEvaluationResult(report_path, report, run.id)


@dataclass(frozen=True, slots=True)
class _LabeledClaim:
    text: str
    supporting_chunk_ids: frozenset[str] | None


def _qa_labeled_claims(case: EvalCase) -> dict[str, _LabeledClaim]:
    raw = case.labels.get("claims")
    if raw is None:
        raw = case.expected.get("claims")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise _case_error(
            case,
            "EVAL_QA_CLAIMS_INVALID",
            "claims labels must map claim ids to labeled claim records",
        )
    labeled: dict[str, _LabeledClaim] = {}
    for claim_id, record in raw.items():
        if not isinstance(claim_id, str) or not claim_id.strip():
            raise _case_error(
                case,
                "EVAL_QA_CLAIMS_INVALID",
                "claim ids must be non-blank strings",
            )
        if not isinstance(record, Mapping):
            raise _case_error(
                case,
                "EVAL_QA_CLAIMS_INVALID",
                f"claim {claim_id} must be an object with a text field",
            )
        text = record.get("text")
        if not isinstance(text, str) or not text.strip():
            raise _case_error(
                case,
                "EVAL_QA_CLAIMS_INVALID",
                f"claim {claim_id} needs a non-blank text label",
            )
        supporting_raw = record.get("supporting_chunk_ids")
        supporting: frozenset[str] | None = None
        if supporting_raw is not None:
            supporting = _canonical_chunk_id_set(
                case, supporting_raw, f"claims.{claim_id}.supporting_chunk_ids"
            )
        labeled[claim_id.strip()] = _LabeledClaim(
            text=text.strip(), supporting_chunk_ids=supporting
        )
    return labeled


def _qa_judgments(
    response: Any,
    *,
    labeled_claims: Mapping[str, _LabeledClaim],
    scope: frozenset[str],
) -> list[dict[str, Any]]:
    """Count every generated claim/citation pair, including unaligned pairs.

    This is a conservative lexical attribution proxy, not semantic entailment.
    Gold source membership alone cannot align a generated statement to a claim.
    Unaligned statements remain in the denominator and receive no coverage credit.
    """

    evidence_text = {item.chunk_id: item.text for item in response.evidence}
    citations_by_label = {item.label: item for item in response.citations}
    judgments: list[dict[str, Any]] = []
    for index, generated in enumerate(response.claims):
        aligned = [
            (claim_id, labeled)
            for claim_id, labeled in labeled_claims.items()
            if lexical_claim_support(labeled.text, generated.text)
            and lexical_claim_support(generated.text, labeled.text)
        ]
        for label in generated.citation_labels:
            citation = citations_by_label.get(label)
            chunk_id = citation.chunk_id if citation is not None else f"unknown-{label}"
            candidates = [
                (claim_id, labeled)
                for claim_id, labeled in aligned
                if labeled.supporting_chunk_ids is None
                or chunk_id in labeled.supporting_chunk_ids
            ]
            claim_id = (
                candidates[0][0] if candidates else f"__unaligned_generated_{index}"
            )
            exists = citation is not None and chunk_id in evidence_text
            binding_valid = citation is not None and index in citation.claim_indices
            supports = bool(candidates) and lexical_claim_support(
                generated.text, evidence_text.get(chunk_id, "")
            )
            judgments.append(
                {
                    "claim_id": claim_id,
                    # Preserve one record per generated pair, even when two
                    # generated claims align to the same gold claim and source.
                    "citation_id": f"{chunk_id}#generated-{index}",
                    "citation_exists": exists,
                    "belongs_to_expected_scope": chunk_id in scope,
                    "supports_claim": supports and binding_valid,
                }
            )
    return judgments


async def run_end_to_end_qa_evaluation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    dataset_id: uuid.UUID,
    index_version: int,
    output_dir: str | Path,
    provider_factory: EvaluationProviderFactory | None = None,
    chat_adapter_factory: Callable[[], Any] | None = None,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
    report_version: str | None = None,
    prompt_version: str = "coursepilot-trusted-core/grounded-render-v1",
    generated_at: datetime | None = None,
    hardware: Mapping[str, Any] | None = None,
    route_top_k: int = 20,
    fusion_top_k: int = 20,
    final_top_k: int = 10,
    rrf_k: int = 60,
    reranker_timeout_seconds: float = 15.0,
) -> CaseEvaluationResult:
    """Run the real TrustedAgentCore QA pipeline against a frozen QA dataset.

    The pipeline is the production guard→route→retrieve→grade→generate→verify
    chain. Refusals are read from the INSUFFICIENT_EVIDENCE status, and citation
    judgments are derived deterministically from frozen claim labels.
    """

    if index_version < 1:
        raise ValueError("index_version must be positive")
    generated = (generated_at or datetime.now(UTC)).astimezone(UTC)
    commit, dirty = _resolve_git_provenance(git_commit, git_dirty)
    hardware_details = dict(hardware or _hardware_provenance())

    async with session_factory() as session:
        dataset, course_index, cases, teacher = await _case_evaluation_context(
            session,
            dataset_id=dataset_id,
            index_version=index_version,
            expected_type=EvalDatasetType.END_TO_END_QA,
            require_retrieval_components=True,
        )
        if course_index.status != CourseIndexStatus.ACTIVE:
            raise EvaluationRunnerError(
                "EVAL_QA_INDEX_NOT_ACTIVE",
                "End-to-end QA measures the student-facing pipeline and requires an ACTIVE index",
            )
        for case in cases:
            _case_query(case)
            if not _qa_labeled_claims(case):
                raise _case_error(
                    case,
                    "EVAL_QA_CLAIMS_MISSING",
                    "QA cases must label claims under labels.claims or expected.claims",
                )
            if not isinstance(case.expected.get("is_answerable"), bool):
                raise _case_error(
                    case,
                    "EVAL_QA_ANSWERABILITY_MISSING",
                    "QA cases must label expected.is_answerable as a boolean",
                )

        factory = provider_factory or DefaultEvaluationProviderFactory()
        engine = session.get_bind()
        providers = factory.build(
            session_factory=session_factory,
            settings=settings,
            dialect_name=engine.dialect.name,
            course_index=course_index,
        )
        if not providers.model_versions:
            raise EvaluationRunnerError(
                "EVAL_MODEL_PROVENANCE_MISSING",
                "The provider factory must report model versions",
            )
        chat_adapter = (
            chat_adapter_factory()
            if chat_adapter_factory is not None
            else build_openai_chat_adapter(settings)
        )
        chat_model = getattr(chat_adapter, "model", None) or NO_GENERATION_QA_VERSION
        retrieval_config = HybridRetrievalConfig(
            route_top_k=route_top_k,
            fusion_top_k=fusion_top_k,
            final_top_k=final_top_k,
            rrf_k=rrf_k,
            reranker_timeout_seconds=reranker_timeout_seconds,
        )
        hybrid = HybridRetriever(
            dense=providers.dense,
            lexical=providers.lexical,
            reranker=providers.reranker,
            config=retrieval_config,
        )
        runner_parameters = {
            "route_top_k": route_top_k,
            "fusion_top_k": fusion_top_k,
            "final_top_k": final_top_k,
            "rrf_k": rrf_k,
            "reranker_timeout_seconds": reranker_timeout_seconds,
            "router_model": DETERMINISTIC_ROUTER_VERSION,
            "grounding": LEXICAL_GROUNDING_VERSION,
        }
        model_versions = {
            **dict(providers.model_versions),
            "router": DETERMINISTIC_ROUTER_VERSION,
            "chat": str(chat_model),
            "grounding": LEXICAL_GROUNDING_VERSION,
        }

        case_results: dict[str, Any] = {}
        started = time.perf_counter()
        for case in cases:
            query = _case_query(case)
            labeled_claims = _qa_labeled_claims(case)
            scope_raw = case.expected.get("relevant_chunk_ids")
            scope = (
                _canonical_chunk_id_set(case, scope_raw, "expected.relevant_chunk_ids")
                if scope_raw is not None
                else frozenset(
                    chunk_id
                    for labeled in labeled_claims.values()
                    for chunk_id in (labeled.supporting_chunk_ids or ())
                )
            )
            if not scope:
                raise _case_error(
                    case,
                    "EVAL_QA_EVIDENCE_SCOPE_MISSING",
                    "QA cases must label evidence scope via expected.relevant_chunk_ids "
                    "or claim supporting_chunk_ids",
                )
            raw_student_id = case.input.get("student_id")
            student_id: str | None = None
            if raw_student_id is not None:
                student_id = str(
                    _required_uuid(
                        case, raw_student_id, "EVAL_QA_STUDENT_INVALID", "student_id"
                    )
                )
            try:
                request = AgentRequest(
                    course_id=str(dataset.course_id),
                    query=query,
                    student_id=student_id,
                    active_index_version=str(index_version),
                )
            except ValidationError as exc:
                raise _case_error(
                    case,
                    "EVAL_CASE_INPUT_INVALID",
                    "The QA case input cannot build an AgentRequest",
                ) from exc

            trace_id = uuid.uuid4()
            evidence_retriever = CourseMaterialEvidenceRetriever(
                session,
                backend=hybrid,
                course_id=dataset.course_id,
                index_version=index_version,
                trace_id=trace_id,
            )
            core = TrustedAgentCore(
                retriever=evidence_retriever,
                chat_adapter=chat_adapter,
                router=ValidatedIntentRouter(),
            )
            case_started = time.perf_counter()
            response = await core.run(request)
            if response.status == AgentStatus.RETRIEVAL_UNAVAILABLE:
                raise _case_error(
                    case,
                    "EVAL_QA_RETRIEVAL_UNAVAILABLE",
                    "The QA pipeline could not retrieve course evidence",
                )
            if response.status == AgentStatus.BUDGET_EXCEEDED:
                raise _case_error(
                    case,
                    "EVAL_QA_BUDGET_EXCEEDED",
                    "The QA pipeline exceeded its safety budget",
                )
            refused = response.status == AgentStatus.INSUFFICIENT_EVIDENCE
            case_results[case.case_key] = {
                "refused": refused,
                "agent_status": response.status.value,
                "routed_intent": response.route.intent.value,
                "citations": _qa_judgments(
                    response, labeled_claims=labeled_claims, scope=scope
                ),
                "answer": response.answer,
                "warnings": list(response.warnings),
                "retrieval_trace_count": len(evidence_retriever.traces),
                "latency_ms": round((time.perf_counter() - case_started) * 1000, 3),
                "citation_judgment_version": "generated-pair-lexical-v2",
            }
        duration_ms = (time.perf_counter() - started) * 1000

        snapshot = _domain_snapshot(
            dataset, cases, frozen_at=_require_frozen_at(dataset)
        )
        selected_report_version = report_version or _report_version(
            dataset, index_version, generated
        )
        run = await _persist_case_run(
            session,
            dataset=dataset,
            teacher=teacher,
            index_version=index_version,
            model_versions=model_versions,
            prompt_version=prompt_version,
            runner_parameters=runner_parameters,
            hardware_details=hardware_details,
            git_commit=commit,
            git_dirty=dirty,
            selected_report_version=selected_report_version,
            duration_ms=duration_ms,
            case_results=case_results,
        )
        report = _case_report_payload(
            dataset=dataset,
            course_index=course_index,
            snapshot=snapshot,
            selected_report_version=selected_report_version,
            generated=generated,
            git_commit=commit,
            git_dirty=dirty,
            model_versions=model_versions,
            prompt_version=prompt_version,
            runner_parameters=runner_parameters,
            hardware_details=hardware_details,
            run=run,
            case_results=case_results,
        )
        report_path = _write_report(output_dir, selected_report_version, report)
        return CaseEvaluationResult(report_path, report, run.id)


async def run_case_evaluation(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    dataset_id: uuid.UUID,
    dataset_type: EvalDatasetType,
    index_version: int,
    output_dir: str | Path,
    provider_factory: EvaluationProviderFactory | None = None,
    chat_adapter_factory: Callable[[], Any] | None = None,
    git_commit: str | None = None,
    git_dirty: bool | None = None,
    report_version: str | None = None,
    prompt_version: str | None = None,
    generated_at: datetime | None = None,
    hardware: Mapping[str, Any] | None = None,
    route_top_k: int = 20,
    fusion_top_k: int = 20,
    final_top_k: int = 10,
    rrf_k: int = 60,
    reranker_timeout_seconds: float = 15.0,
) -> CaseEvaluationResult:
    """Dispatch the server-side case runner for every non-retrieval dataset type."""

    if dataset_type == EvalDatasetType.INTENT_ROUTING:
        return await run_intent_routing_evaluation(
            session_factory=session_factory,
            settings=settings,
            dataset_id=dataset_id,
            index_version=index_version,
            output_dir=output_dir,
            git_commit=git_commit,
            git_dirty=git_dirty,
            report_version=report_version,
            prompt_version=prompt_version or "no-generation/intent-routing-eval-v1",
            generated_at=generated_at,
            hardware=hardware,
        )
    if dataset_type == EvalDatasetType.LEARNING_PATH:
        return await run_learning_path_evaluation(
            session_factory=session_factory,
            settings=settings,
            dataset_id=dataset_id,
            index_version=index_version,
            output_dir=output_dir,
            git_commit=git_commit,
            git_dirty=git_dirty,
            report_version=report_version,
            prompt_version=prompt_version or "no-generation/learning-path-eval-v1",
            generated_at=generated_at,
            hardware=hardware,
        )
    if dataset_type == EvalDatasetType.END_TO_END_QA:
        return await run_end_to_end_qa_evaluation(
            session_factory=session_factory,
            settings=settings,
            dataset_id=dataset_id,
            index_version=index_version,
            output_dir=output_dir,
            provider_factory=provider_factory,
            chat_adapter_factory=chat_adapter_factory,
            git_commit=git_commit,
            git_dirty=git_dirty,
            report_version=report_version,
            prompt_version=prompt_version
            or "coursepilot-trusted-core/grounded-render-v1",
            generated_at=generated_at,
            hardware=hardware,
            route_top_k=route_top_k,
            fusion_top_k=fusion_top_k,
            final_top_k=final_top_k,
            rrf_k=rrf_k,
            reranker_timeout_seconds=reranker_timeout_seconds,
        )
    raise EvaluationRunnerError(
        "EVAL_DATASET_TYPE_INVALID",
        "RETRIEVAL datasets must use run_three_baselines",
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run dense_only, hybrid_rerank, and kg_personalized against one "
            "frozen CoursePilot retrieval dataset."
        )
    )
    parser.add_argument("--dataset-id", required=True, type=uuid.UUID)
    parser.add_argument("--index-version", required=True, type=int)
    parser.add_argument("--output-dir", default="evaluation-reports")
    parser.add_argument("--report-version")
    parser.add_argument("--git-commit")
    parser.add_argument("--prompt-version", default="no-generation/retrieval-eval-v1")
    parser.add_argument("--route-top-k", type=int, default=20)
    parser.add_argument("--fusion-top-k", type=int, default=20)
    parser.add_argument("--final-top-k", type=int, default=10)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--reranker-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--graph-depth", type=int, default=2)
    parser.add_argument("--kg-weight", type=float, default=1.0)
    return parser


async def _async_main(args: argparse.Namespace) -> ThreeBaselineResult:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        return await run_three_baselines(
            session_factory=create_session_factory(engine),
            settings=settings,
            dataset_id=args.dataset_id,
            index_version=args.index_version,
            output_dir=args.output_dir,
            git_commit=args.git_commit,
            report_version=args.report_version,
            prompt_version=args.prompt_version,
            route_top_k=args.route_top_k,
            fusion_top_k=args.fusion_top_k,
            final_top_k=args.final_top_k,
            rrf_k=args.rrf_k,
            reranker_timeout_seconds=args.reranker_timeout_seconds,
            graph_depth=args.graph_depth,
            kg_weight=args.kg_weight,
        )
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_async_main(args))
    except EvaluationRunnerError as exc:
        payload: dict[str, Any] = {"error": {"code": exc.code, "message": str(exc)}}
        if exc.details:
            payload["error"]["details"] = exc.details
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "report_path": str(result.report_path),
                "eval_run_ids": {
                    mode: str(run_id) for mode, run_id in result.eval_run_ids.items()
                },
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
