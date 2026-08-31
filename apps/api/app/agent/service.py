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

from app.errors import AppError
from app.learning.service import LearningService
from app.models import (
    Chunk,
    ConceptCandidate,
    CourseIndex,
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    IndexComponentStatus,
    MasteryState,
    RecordStatus,
    ReviewStatus,
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
from .schemas import (
    AgentRequest,
    AgentResponse,
    AgentStatus,
    BudgetSnapshot,
    Evidence,
    Intent,
    IntentRoute,
)


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
    published chunks restricted to the document versions the pinned index
    covers, and never reads text outside the requested course.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        course_id: uuid.UUID,
        index_version: int,
        covered_version_ids: Sequence[uuid.UUID | str] | None = None,
    ) -> None:
        self.session = session
        self.course_id = course_id
        self.index_version = index_version
        # Missing coverage means an index predates the corpus manifest. Treat
        # it as an empty corpus instead of restoring the old course-wide query,
        # which would let newly published document versions enter pinned chats.
        self._covered_version_ids: frozenset[uuid.UUID] = frozenset(
            uuid.UUID(str(value)) for value in (covered_version_ids or ()) if value
        )
        self._index: LightweightBM25Index | None = None

    async def _load(self) -> LightweightBM25Index:
        if self._index is not None:
            return self._index
        filters = [
            Document.course_id == self.course_id,
            Document.status == DocumentStatus.ACTIVE,
            DocumentVersion.status == DocumentVersionStatus.PUBLISHED,
            Chunk.status == RecordStatus.ACTIVE,
            Chunk.deleted_at.is_(None),
            DocumentVersion.deleted_at.is_(None),
            Document.deleted_at.is_(None),
        ]
        # Restrict the fallback corpus to the versions the pinned index
        # actually built with; otherwise content published after this version
        # would silently enter an older session's evidence. An empty set is the
        # intentional fail-closed behavior for pre-manifest indexes.
        filters.append(DocumentVersion.id.in_(self._covered_version_ids))
        rows = (
            await self.session.execute(
                select(Chunk, DocumentVersion, Document)
                .join(DocumentVersion, DocumentVersion.id == Chunk.version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(*filters)
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

        covered_version_ids = frozenset(
            uuid.UUID(str(value))
            for value in (
                await self.session.scalar(
                    select(CourseIndex.covered_document_version_ids).where(
                        CourseIndex.course_id == self.course_id,
                        CourseIndex.version == self.index_version,
                        CourseIndex.deleted_at.is_(None),
                    )
                )
                or ()
            )
        )
        if not covered_version_ids:
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
                    DocumentVersion.id.in_(covered_version_ids),
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
                    score=self._normalized_score(candidate),
                )
            )
            if len(evidence) >= top_k:
                break
        return evidence

    @staticmethod
    def _normalized_score(candidate: Any) -> float:
        if isinstance(candidate, Evidence):
            return candidate.score

        # A successful reranker is the final relevance judgment and must take
        # precedence over route-level dense/BM25 scores carried in metadata.
        # Route scores are consulted only when reranking was skipped or failed.
        if isinstance(candidate, FusedCandidate) and candidate.rerank_score is not None:
            value = float(candidate.rerank_score)
            if math.isfinite(value):
                if 0.0 <= value <= 1.0:
                    return value
                if value >= 0.0:
                    return 1.0 / (1.0 + math.exp(-value))
                exped = math.exp(value)
                return exped / (1.0 + exped)

        metadata = getattr(candidate, "metadata", {})
        if isinstance(candidate, Mapping):
            metadata = candidate.get("metadata", {})
        if isinstance(metadata, Mapping):
            explicit = metadata.get("normalized_score")
            if isinstance(explicit, (int, float)) and not isinstance(explicit, bool):
                value = float(explicit)
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
        # BM25 and RRF outputs are not calibrated probabilities. A candidate
        # whose route did not supply a normalized score must not cross the
        # evidence gate: the previous rank-based fallback (max(0.56, ...)) let
        # every lexical hit pass EvidencePolicy.minimum_score and hollowed out
        # insufficient-evidence refusal.
        return 0.0


class CourseLearningBusinessHandler:
    """Execute deterministic student-learning intents against reviewed data."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        course_id: uuid.UUID,
        student_id: uuid.UUID,
    ) -> None:
        self.session = session
        self.course_id = course_id
        self.student_id = student_id
        self.learning = LearningService()

    @staticmethod
    def _response(**values: Any) -> AgentResponse:
        # TrustedAgentCore replaces this intermediate snapshot with the real
        # turn budget before returning the response.
        return AgentResponse(
            budget=BudgetSnapshot(
                query_rewrites=0,
                tool_calls=0,
                steps=0,
                max_query_rewrites=0,
                max_tool_calls=0,
                max_steps=0,
            ),
            **values,
        )

    async def handle(
        self, *, request: AgentRequest, route: IntentRoute
    ) -> AgentResponse | None:
        if route.intent == Intent.QUIZ:
            return await self._quiz(route)
        if route.intent == Intent.LEARNING_PATH:
            return await self._learning_path(route)
        if route.intent == Intent.DIAGNOSE:
            return await self._diagnose(route)
        return None

    async def _student(self):
        from app.models import User

        student = await self.session.get(User, self.student_id)
        if student is None:
            raise PermissionError("student does not exist")
        return student

    @staticmethod
    def _target_id(route: IntentRoute) -> uuid.UUID | None:
        for value in route.target_concepts:
            try:
                return uuid.UUID(value)
            except ValueError:
                continue
        return None

    async def _quiz(self, route: IntentRoute) -> AgentResponse:
        try:
            quiz = await self.learning.next_quiz(
                self.session,
                self.course_id,
                await self._student(),
                concept_id=self._target_id(route),
            )
        except AppError as exc:
            code = getattr(exc, "code", "QUIZ_UNAVAILABLE")
            return self._response(
                status=AgentStatus.ROUTED,
                route=route,
                answer="当前没有符合条件的已审核题目。",
                error_code=code,
                error_message="当前没有符合条件的已审核题目。",
            )
        options = "\n".join(
            f"{chr(65 + index)}. {option}" for index, option in enumerate(quiz.options)
        )
        return self._response(
            status=AgentStatus.ROUTED,
            route=route,
            answer=f"练习题：{quiz.question}\n{options}",
            warnings=[f"QUIZ_ITEM_ID:{quiz.id}"],
        )

    async def _active_concepts(self) -> list[ConceptCandidate]:
        index = await self.learning._active_index(self.session, self.course_id)
        return list(
            (
                await self.session.scalars(
                    select(ConceptCandidate)
                    .where(
                        ConceptCandidate.course_id == self.course_id,
                        ConceptCandidate.index_id == index.id,
                        ConceptCandidate.status == ReviewStatus.APPROVED,
                        ConceptCandidate.deleted_at.is_(None),
                    )
                    .order_by(ConceptCandidate.name, ConceptCandidate.id)
                )
            ).all()
        )

    async def _mastery(self) -> dict[uuid.UUID, MasteryState]:
        rows = (
            await self.session.scalars(
                select(MasteryState).where(
                    MasteryState.course_id == self.course_id,
                    MasteryState.student_id == self.student_id,
                    MasteryState.status == RecordStatus.ACTIVE,
                )
            )
        ).all()
        return {row.concept_id: row for row in rows}

    async def _learning_path(self, route: IntentRoute) -> AgentResponse:
        concepts = await self._active_concepts()
        if not concepts:
            return self._response(
                status=AgentStatus.ROUTED,
                route=route,
                answer="当前课程还没有可用于生成学习路径的已审核知识点。",
                error_code="LEARNING_PATH_UNAVAILABLE",
                error_message="当前课程还没有可用于生成学习路径的已审核知识点。",
            )
        target_id = self._target_id(route)
        if target_id is None:
            mastery = await self._mastery()
            target_id = min(
                concepts,
                key=lambda item: (
                    mastery.get(item.id).mastery if item.id in mastery else 0.5,
                    item.name,
                ),
            ).id
        try:
            result = await self.learning.learning_path(
                self.session,
                self.course_id,
                await self._student(),
                target_concept_id=target_id,
                max_depth=4,
            )
        except AppError as exc:
            code = getattr(exc, "code", "LEARNING_PATH_UNAVAILABLE")
            return self._response(
                status=AgentStatus.ROUTED,
                route=route,
                answer="暂时无法为该知识点生成学习路径。",
                error_code=code,
                error_message="暂时无法为该知识点生成学习路径。",
            )
        lines = [
            f"{position}. {step.concept.name}（掌握度 {step.mastery:.0%}）"
            for position, step in enumerate(result.steps, start=1)
        ]
        return self._response(
            status=AgentStatus.ROUTED,
            route=route,
            answer="建议学习顺序：\n" + "\n".join(lines),
        )

    async def _diagnose(self, route: IntentRoute) -> AgentResponse:
        concepts = await self._active_concepts()
        mastery = await self._mastery()
        assessed = [
            (concept, mastery[concept.id])
            for concept in concepts
            if concept.id in mastery and mastery[concept.id].attempt_count > 0
        ]
        if not assessed:
            return self._response(
                status=AgentStatus.ROUTED,
                route=route,
                answer="目前还没有有效的已审核题目作答记录，完成练习后才能诊断薄弱点。",
            )
        weakest = sorted(assessed, key=lambda item: (item[1].mastery, item[0].name))[:3]
        lines = [
            f"{position}. {concept.name}：掌握度 {state.mastery:.0%}，"
            f"有效作答 {state.attempt_count} 次"
            for position, (concept, state) in enumerate(weakest, start=1)
        ]
        return self._response(
            status=AgentStatus.ROUTED,
            route=route,
            answer="当前优先复习的薄弱知识点：\n" + "\n".join(lines),
        )


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
            covered_version_ids=index.covered_document_version_ids,
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
        business_handler=CourseLearningBusinessHandler(
            session,
            course_id=course_id,
            student_id=student_id,
        ),
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
