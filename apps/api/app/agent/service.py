from __future__ import annotations

import asyncio
import inspect
import math
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Chunk,
    CourseIndex,
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    IndexComponentStatus,
    RecordStatus,
)
from app.retrieval import (
    FusedCandidate,
    HybridRetriever,
    LexicalCandidate,
    LexicalDocument,
    LightweightBM25Index,
    RetrievalResult,
    RetrievalUnavailableError,
)

from .core import ChatAdapter, TrustedAgentCore
from .schemas import AgentRequest, AgentResponse, Evidence, Intent


async def _invoke(method: Any, /, *args: Any, **kwargs: Any) -> Any:
    """Invoke either a synchronous or asynchronous adapter safely."""

    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    result = await asyncio.to_thread(method, *args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class DatabaseLexicalRetriever:
    """Small DB-backed lexical route for a published course corpus.

    The persisted BM25 artifact remains the preferred production route. This
    implementation is also useful while a process cache is cold and in tests: it
    builds the same deterministic lightweight BM25 index from authoritative,
    published chunks and never reads text outside the requested course.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        course_id: uuid.UUID,
        index_version: int,
    ) -> None:
        self.session = session
        self.course_id = course_id
        self.index_version = index_version
        self._index: LightweightBM25Index | None = None

    async def _load(self) -> LightweightBM25Index:
        if self._index is not None:
            return self._index
        rows = (
            await self.session.execute(
                select(Chunk, DocumentVersion, Document)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Document.course_id == self.course_id,
                    Document.status == DocumentStatus.ACTIVE,
                    DocumentVersion.status == DocumentVersionStatus.PUBLISHED,
                    Chunk.status == RecordStatus.ACTIVE,
                    Chunk.deleted_at.is_(None),
                    DocumentVersion.deleted_at.is_(None),
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.id, DocumentVersion.version, Chunk.ordinal)
            )
        ).all()
        documents = [
            LexicalDocument(
                chunk_id=str(chunk.id),
                content=chunk.content,
                metadata={
                    "document": document.logical_name,
                    "document_version": str(version.version),
                    "section": " / ".join(chunk.section_path)
                    if chunk.section_path
                    else None,
                    "page": chunk.page,
                },
            )
            for chunk, version, document in rows
        ]
        self._index = LightweightBM25Index(documents)
        return self._index

    async def search(
        self,
        *,
        course_id: str,
        index_version: str,
        query: str,
        top_k: int = 20,
        filters: Mapping[str, Any] | None = None,
    ) -> list[LexicalCandidate]:
        if str(self.course_id) != str(course_id) or str(self.index_version) != str(
            index_version
        ):
            raise PermissionError("lexical retrieval scope does not match the session")
        if filters and str(filters.get("index_version", index_version)) != str(
            self.index_version
        ):
            raise PermissionError("lexical retrieval index does not match the session")
        index = await self._load()
        return index.search(query, top_k=top_k)


class FallbackLexicalRetriever:
    """Use a configured lexical artifact, falling back to published DB chunks."""

    def __init__(self, primary: Any, fallback: DatabaseLexicalRetriever) -> None:
        self.primary = primary
        self.fallback = fallback

    async def search(self, **kwargs: Any) -> Sequence[LexicalCandidate]:
        try:
            return await _invoke(self.primary.search, **kwargs)
        except Exception:  # noqa: BLE001 - an explicit, scoped degradation route
            return await self.fallback.search(**kwargs)


@dataclass(frozen=True, slots=True)
class TrustedTurnResult:
    trace_id: uuid.UUID
    response: AgentResponse
    retrieval_traces: tuple[dict[str, Any], ...]
    candidate_count: int

    @property
    def retrieval_query(self) -> str:
        if not self.retrieval_traces:
            return ""
        return str(self.retrieval_traces[-1].get("query", ""))


class CourseMaterialEvidenceRetriever:
    """Adapt hybrid retrieval output to the Agent's authoritative Evidence schema.

    Candidate metadata and text are intentionally not trusted. Candidate IDs are
    reloaded through course-scoped joins before they are allowed to cross the
    evidence boundary.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        backend: Any,
        course_id: uuid.UUID,
        index_version: int,
        trace_id: uuid.UUID,
    ) -> None:
        self.session = session
        self.backend = backend
        self.course_id = course_id
        self.index_version = index_version
        self.trace_id = trace_id
        self.traces: list[dict[str, Any]] = []
        self.candidate_count = 0

    async def search_course_material(
        self,
        *,
        course_id: str,
        query: str,
        filters: Mapping[str, Any],
        top_k: int,
    ) -> list[Evidence]:
        if str(self.course_id) != str(course_id):
            raise PermissionError("retrieval course does not match the session")
        requested_version = str(filters.get("index_version", ""))
        if requested_version != str(self.index_version):
            raise PermissionError("retrieval index does not match the session")

        try:
            if hasattr(self.backend, "retrieve"):
                raw = await _invoke(
                    self.backend.retrieve,
                    course_id=course_id,
                    index_version=str(self.index_version),
                    query=query,
                    top_k=top_k,
                    filters=None,
                    trace_id=str(self.trace_id),
                )
            elif hasattr(self.backend, "search_course_material"):
                raw = await _invoke(
                    self.backend.search_course_material,
                    course_id=course_id,
                    query=query,
                    filters={"index_version": str(self.index_version)},
                    top_k=top_k,
                )
            else:
                raise TypeError("retrieval backend exposes no supported search method")
        except RetrievalUnavailableError as exc:
            self.traces.append(exc.trace.to_dict())
            raise

        if isinstance(raw, RetrievalResult):
            candidates: Sequence[Any] = raw.candidates
            self.traces.append(raw.trace.to_dict())
        else:
            if isinstance(raw, (str, bytes, Mapping)) or raw is None:
                raise TypeError("retrieval backend returned an invalid result")
            candidates = list(raw)
            self.traces.append(
                {
                    "trace_id": str(self.trace_id),
                    "course_id": str(self.course_id),
                    "index_version": str(self.index_version),
                    "query": query,
                    "degraded": False,
                    "degraded_routes": [],
                    "stages": {"final": {"status": "success"}},
                }
            )

        self.candidate_count += len(candidates)
        return await self._hydrate(candidates, top_k=top_k)

    async def _hydrate(
        self, candidates: Sequence[Any], *, top_k: int
    ) -> list[Evidence]:
        ordered: list[tuple[uuid.UUID, Any, int]] = []
        seen: set[uuid.UUID] = set()
        for rank, candidate in enumerate(candidates, start=1):
            raw_chunk_id = (
                candidate.chunk_id
                if hasattr(candidate, "chunk_id")
                else candidate.get("chunk_id")
                if isinstance(candidate, Mapping)
                else None
            )
            try:
                chunk_id = uuid.UUID(str(raw_chunk_id))
            except (TypeError, ValueError):
                continue
            if chunk_id in seen:
                continue
            seen.add(chunk_id)
            ordered.append((chunk_id, candidate, rank))
        if not ordered:
            return []

        rows = (
            await self.session.execute(
                select(Chunk, DocumentVersion, Document)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(
                    Chunk.id.in_([item[0] for item in ordered]),
                    Document.course_id == self.course_id,
                    Document.status == DocumentStatus.ACTIVE,
                    DocumentVersion.status == DocumentVersionStatus.PUBLISHED,
                    Chunk.status == RecordStatus.ACTIVE,
                    Chunk.deleted_at.is_(None),
                    DocumentVersion.deleted_at.is_(None),
                    Document.deleted_at.is_(None),
                )
            )
        ).all()
        authoritative = {
            chunk.id: (chunk, version, document) for chunk, version, document in rows
        }

        evidence: list[Evidence] = []
        for chunk_id, candidate, rank in ordered:
            row = authoritative.get(chunk_id)
            if row is None:
                continue
            chunk, version, document = row
            evidence.append(
                Evidence(
                    course_id=str(self.course_id),
                    index_version=str(self.index_version),
                    chunk_id=str(chunk.id),
                    document=document.logical_name,
                    document_version=str(version.version),
                    section=" / ".join(chunk.section_path)
                    if chunk.section_path
                    else None,
                    page=chunk.page,
                    text=chunk.content,
                    score=self._normalized_score(candidate, rank),
                )
            )
            if len(evidence) >= top_k:
                break
        return evidence

    @staticmethod
    def _normalized_score(candidate: Any, rank: int) -> float:
        if isinstance(candidate, Evidence):
            return candidate.score
        metadata = getattr(candidate, "metadata", {})
        if isinstance(candidate, Mapping):
            metadata = candidate.get("metadata", {})
        if isinstance(metadata, Mapping):
            explicit = metadata.get("normalized_score")
            if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
                value = float(explicit)
                if math.isfinite(value) and 0.0 <= value <= 1.0:
                    return value

        # RRF and BM25 scores are not calibrated probabilities. A positive ranked
        # match crosses the Agent boundary using a conservative, rank-based score;
        # empty lexical searches still produce no evidence and therefore abstain.
        if isinstance(candidate, FusedCandidate) and candidate.rerank_score is not None:
            value = float(candidate.rerank_score)
            if math.isfinite(value) and 0.0 <= value <= 1.0:
                return value
        raw_score = (
            getattr(candidate, "score", None)
            if not isinstance(candidate, Mapping)
            else candidate.get("score")
        )
        if (
            not isinstance(candidate, FusedCandidate)
            and isinstance(raw_score, (int, float))
            and not isinstance(raw_score, bool)
        ):
            value = float(raw_score)
            if math.isfinite(value) and 0.0 <= value <= 1.0:
                return value
        return max(0.56, 1.0 - (rank - 1) * 0.06)


def build_retrieval_backend(
    app_state: Any,
    session: AsyncSession,
    *,
    index: CourseIndex,
) -> Any:
    """Build a version-scoped hybrid backend from configured app adapters."""

    dense_ready = index.dense_status == IndexComponentStatus.READY
    lexical_ready = index.lexical_status == IndexComponentStatus.READY
    if not dense_ready and not lexical_ready:
        return HybridRetriever(dense=None, lexical=None)

    explicitly_composed = getattr(app_state, "chat_retriever", None) or getattr(
        app_state, "hybrid_retriever", None
    )
    if explicitly_composed is not None:
        return explicitly_composed

    dense = getattr(app_state, "dense_retriever", None) if dense_ready else None
    lexical: Any | None = None
    if lexical_ready:
        database_lexical = DatabaseLexicalRetriever(
            session,
            course_id=index.course_id,
            index_version=index.version,
        )
        configured_lexical = getattr(app_state, "lexical_retriever", None) or getattr(
            app_state, "lexical_index_manager", None
        )
        lexical = (
            FallbackLexicalRetriever(configured_lexical, database_lexical)
            if configured_lexical is not None
            else database_lexical
        )
    reranker = getattr(app_state, "reranker", None)
    return HybridRetriever(dense=dense, lexical=lexical, reranker=reranker)


async def run_trusted_turn(
    session: AsyncSession,
    *,
    backend: Any,
    chat_adapter: ChatAdapter | None,
    course_id: uuid.UUID,
    student_id: uuid.UUID,
    index_version: int,
    query: str,
    requested_intent: Intent | None,
    target_concept_ids: Sequence[uuid.UUID],
    trace_id: uuid.UUID | None = None,
) -> TrustedTurnResult:
    run_trace_id = trace_id or uuid.uuid4()
    evidence_retriever = CourseMaterialEvidenceRetriever(
        session,
        backend=backend,
        course_id=course_id,
        index_version=index_version,
        trace_id=run_trace_id,
    )
    core = TrustedAgentCore(
        retriever=evidence_retriever,
        chat_adapter=chat_adapter,
    )
    response = await core.run(
        AgentRequest(
            course_id=str(course_id),
            student_id=str(student_id),
            query=query,
            requested_intent=requested_intent,
            target_concepts=[str(item) for item in target_concept_ids],
            active_index_version=str(index_version),
        )
    )
    return TrustedTurnResult(
        trace_id=run_trace_id,
        response=response,
        retrieval_traces=tuple(evidence_retriever.traces),
        candidate_count=evidence_retriever.candidate_count,
    )
