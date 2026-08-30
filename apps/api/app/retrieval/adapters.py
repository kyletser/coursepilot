from __future__ import annotations

import asyncio
import math
import threading
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import aliased

from app.models import (
    Chunk,
    CourseIndex,
    CourseIndexStatus,
    Document,
    DocumentStatus,
    DocumentVersion,
    DocumentVersionStatus,
    RecordStatus,
)

from .types import DenseCandidate


class ModelDependencyUnavailableError(RuntimeError):
    pass


class DenseRetrievalUnavailableError(RuntimeError):
    """The configured database cannot execute the pgvector retrieval route."""


class QueryEmbeddingAdapter(Protocol):
    async def embed_query(self, query: str) -> list[float]: ...


class DenseVectorStore(Protocol):
    def search(
        self,
        *,
        course_id: str,
        index_version: str,
        query_vector: Sequence[float],
        top_k: int,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[DenseCandidate]: ...


def _to_float_vectors(value: Any) -> list[list[float]]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    return [[float(number) for number in vector] for vector in value]


def _to_float_scores(value: Any) -> list[float]:
    if hasattr(value, "tolist"):
        value = value.tolist()
    if isinstance(value, (int, float)):
        return [float(value)]
    return [float(score) for score in value]


class BGEM3EmbeddingAdapter:
    """Lazy local BGE-M3 embedding adapter.

    By default only an already-cached model may be loaded. Production can opt
    into downloading explicitly or inject a compatible model factory. Merely
    constructing this adapter never imports a model library or touches weights.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        *,
        device: str | None = None,
        allow_download: bool = False,
        cache_folder: str | Path | None = None,
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.allow_download = allow_download
        self.cache_folder = str(cache_folder) if cache_folder is not None else None
        self._model_factory = model_factory
        self._model: Any | None = None
        self._load_lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _default_factory(self) -> Any:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ModelDependencyUnavailableError(
                "sentence-transformers is required to load BGE-M3"
            ) from exc
        options: dict[str, Any] = {
            "local_files_only": not self.allow_download,
        }
        if self.cache_folder is not None:
            options["cache_folder"] = self.cache_folder
        if self.device is not None:
            options["device"] = self.device
        return SentenceTransformer(self.model_name, **options)

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                factory = self._model_factory or self._default_factory
                self._model = factory()
        return self._model

    def _embed_sync(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        vectors = model.encode(
            list(texts),
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        result = _to_float_vectors(vectors)
        if len(result) != len(texts):
            raise ValueError("embedding model returned an unexpected vector count")
        return result

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return await asyncio.to_thread(self._embed_sync, tuple(texts))

    async def embed_query(self, query: str) -> list[float]:
        vectors = await self.embed_documents((query,))
        return vectors[0]


class BGERerankerAdapter:
    """Lazy BGE cross-encoder adapter implementing the hybrid score protocol."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        *,
        device: str | None = None,
        allow_download: bool = False,
        cache_folder: str | Path | None = None,
        model_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.model_name = model_name
        self.device = device
        self.allow_download = allow_download
        self.cache_folder = str(cache_folder) if cache_folder is not None else None
        self._model_factory = model_factory
        self._model: Any | None = None
        self._load_lock = threading.Lock()

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def _default_factory(self) -> Any:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise ModelDependencyUnavailableError(
                "sentence-transformers is required to load the BGE reranker"
            ) from exc
        options: dict[str, Any] = {
            "local_files_only": not self.allow_download,
        }
        if self.cache_folder is not None:
            options["cache_folder"] = self.cache_folder
        if self.device is not None:
            options["device"] = self.device
        return CrossEncoder(self.model_name, **options)

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is None:
                factory = self._model_factory or self._default_factory
                self._model = factory()
        return self._model

    def _score_sync(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        model = self._get_model()
        pairs = [(query, document) for document in documents]
        scores = _to_float_scores(model.predict(pairs, show_progress_bar=False))
        if len(scores) != len(documents):
            raise ValueError("reranker returned an unexpected score count")
        return scores

    async def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return await asyncio.to_thread(self._score_sync, query, tuple(documents))


class EmbeddingDenseRetriever:
    """Compose a query embedding adapter with a course-scoped vector store."""

    def __init__(
        self,
        embedding: BGEM3EmbeddingAdapter,
        vector_store: DenseVectorStore,
    ) -> None:
        self.embedding = embedding
        self.vector_store = vector_store

    async def search(
        self,
        *,
        course_id: str,
        index_version: str,
        query: str,
        top_k: int = 20,
        filters: Mapping[str, Any] | None = None,
    ) -> Sequence[DenseCandidate]:
        query_vector = await self.embedding.embed_query(query)
        result = await asyncio.to_thread(
            self.vector_store.search,
            course_id=course_id,
            index_version=index_version,
            query_vector=query_vector,
            top_k=top_k,
            filters=filters,
        )
        return result


class PostgresPgVectorDenseRetriever:
    """Async, course-scoped dense retrieval over the active published index.

    This adapter intentionally owns its sessions instead of sharing the request's
    ``AsyncSession``. Hybrid retrieval runs dense and lexical routes concurrently,
    and SQLAlchemy sessions are not safe for concurrent operations.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        embedding: QueryEmbeddingAdapter,
        *,
        dialect_name: str,
        embedding_dimension: int = 1024,
    ) -> None:
        if embedding_dimension < 1:
            raise ValueError("embedding_dimension must be positive")
        self.session_factory = session_factory
        self.embedding = embedding
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
            # In local SQLite tests this becomes an explicit dense-route failure,
            # allowing HybridRetriever to continue with the lexical route without
            # importing or loading sentence-transformers.
            raise DenseRetrievalUnavailableError(
                "pgvector dense retrieval requires PostgreSQL"
            )
        try:
            scoped_course_id = uuid.UUID(str(course_id))
            scoped_index_version = int(index_version)
        except (TypeError, ValueError) as exc:
            raise ValueError("course_id and index_version must be valid") from exc
        if scoped_index_version < 1:
            raise ValueError("index_version must be positive")
        if not query or not query.strip():
            raise ValueError("query must not be blank")
        if not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        if filters:
            raise ValueError("metadata filters are not supported by dense retrieval")

        query_vector = [
            float(value) for value in await self.embedding.embed_query(query)
        ]
        if len(query_vector) != self.embedding_dimension or any(
            not math.isfinite(value) for value in query_vector
        ):
            raise ValueError("embedding model returned an invalid query vector")

        published_version = aliased(DocumentVersion)
        latest_published_version = (
            select(func.max(published_version.version))
            .where(
                published_version.document_id == Document.id,
                published_version.status == DocumentVersionStatus.PUBLISHED,
                published_version.deleted_at.is_(None),
            )
            .correlate(Document)
            .scalar_subquery()
        )
        active_index_exists = exists(
            select(CourseIndex.id).where(
                CourseIndex.course_id == scoped_course_id,
                CourseIndex.version == scoped_index_version,
                CourseIndex.status == CourseIndexStatus.ACTIVE,
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
                active_index_exists,
                Document.course_id == scoped_course_id,
                Document.status == DocumentStatus.ACTIVE,
                Document.deleted_at.is_(None),
                DocumentVersion.status == DocumentVersionStatus.PUBLISHED,
                DocumentVersion.deleted_at.is_(None),
                DocumentVersion.version == latest_published_version,
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
            cosine_distance = float(raw_distance)
            similarity = 1.0 - cosine_distance
            if not math.isfinite(similarity):
                continue
            normalized_score = max(0.0, min(1.0, (similarity + 1.0) / 2.0))
            candidates.append(
                DenseCandidate(
                    chunk_id=str(chunk.id),
                    score=similarity,
                    content=chunk.content,
                    metadata={
                        "normalized_score": normalized_score,
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


# Friendly aliases for projects that prefer conventional class capitalization.
BgeM3EmbeddingAdapter = BGEM3EmbeddingAdapter
BgeRerankerAdapter = BGERerankerAdapter
