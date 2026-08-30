from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .text import tokenize
from .types import LexicalCandidate

ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_FILENAME = "lexical-index.json"


class LexicalIndexArtifactError(RuntimeError):
    pass


def _safe_path_component(value: str, *, name: str) -> str:
    value = str(value)
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError(f"{name} must be a single non-empty path component")
    return value


@dataclass(frozen=True, slots=True)
class LexicalDocument:
    chunk_id: str
    content: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.chunk_id or not self.chunk_id.strip():
            raise ValueError("chunk_id must not be empty")
        object.__setattr__(self, "metadata", dict(self.metadata))


class LightweightBM25Index:
    """Small, dependency-free BM25 index used behind the lexical interface.

    The artifact stores source documents rather than Python-specific posting
    internals. Loading deterministically rebuilds the in-memory postings, which
    keeps the format portable and leaves room for a delayed ``bm25s`` adapter.
    """

    def __init__(
        self,
        documents: Iterable[LexicalDocument],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("b must be between 0 and 1")
        self.k1 = float(k1)
        self.b = float(b)
        self.documents = tuple(documents)

        identifiers = [document.chunk_id for document in self.documents]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("lexical documents must have unique chunk IDs")

        self._term_frequencies: tuple[Counter[str], ...] = tuple(
            Counter(tokenize(document.content)) for document in self.documents
        )
        self._document_lengths = tuple(
            sum(frequencies.values()) for frequencies in self._term_frequencies
        )
        self._average_document_length = (
            sum(self._document_lengths) / len(self._document_lengths)
            if self._document_lengths
            else 0.0
        )
        postings: dict[str, list[int]] = defaultdict(list)
        for document_number, frequencies in enumerate(self._term_frequencies):
            for term in frequencies:
                postings[term].append(document_number)
        self._postings = dict(postings)

    def search(self, query: str, *, top_k: int = 20) -> list[LexicalCandidate]:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        query_terms = tuple(dict.fromkeys(tokenize(query)))
        if not query_terms or not self.documents:
            return []

        document_count = len(self.documents)
        scores: dict[int, float] = defaultdict(float)
        for term in query_terms:
            matches = self._postings.get(term, ())
            document_frequency = len(matches)
            if not document_frequency:
                continue
            inverse_document_frequency = math.log(
                1.0
                + (document_count - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            for document_number in matches:
                term_frequency = self._term_frequencies[document_number][term]
                document_length = self._document_lengths[document_number]
                normalized_length = (
                    document_length / self._average_document_length
                    if self._average_document_length
                    else 0.0
                )
                denominator = term_frequency + self.k1 * (
                    1.0 - self.b + self.b * normalized_length
                )
                scores[document_number] += inverse_document_frequency * (
                    term_frequency * (self.k1 + 1.0) / denominator
                )

        ranked = sorted(
            scores.items(),
            key=lambda item: (-item[1], self.documents[item[0]].chunk_id),
        )[:top_k]
        return [
            LexicalCandidate(
                chunk_id=self.documents[document_number].chunk_id,
                score=score,
                content=self.documents[document_number].content,
                metadata=self.documents[document_number].metadata,
            )
            for document_number, score in ranked
            if score > 0
        ]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": ARTIFACT_SCHEMA_VERSION,
            "engine": "coursepilot-lightweight-bm25",
            "k1": self.k1,
            "b": self.b,
            "documents": [
                {
                    "chunk_id": document.chunk_id,
                    "content": document.content,
                    "metadata": dict(document.metadata),
                }
                for document in self.documents
            ],
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> LightweightBM25Index:
        if payload.get("schema_version") != ARTIFACT_SCHEMA_VERSION:
            raise LexicalIndexArtifactError("unsupported lexical artifact version")
        if payload.get("engine") != "coursepilot-lightweight-bm25":
            raise LexicalIndexArtifactError("unsupported lexical artifact engine")
        raw_documents = payload.get("documents")
        if not isinstance(raw_documents, list):
            raise LexicalIndexArtifactError("lexical artifact documents are invalid")
        try:
            documents = [
                LexicalDocument(
                    chunk_id=item["chunk_id"],
                    content=item["content"],
                    metadata=item.get("metadata", {}),
                )
                for item in raw_documents
            ]
            return cls(
                documents,
                k1=float(payload.get("k1", 1.5)),
                b=float(payload.get("b", 0.75)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LexicalIndexArtifactError("lexical artifact is malformed") from exc


class LexicalIndexArtifactStore:
    """Persist course-version artifacts through same-filesystem atomic replace."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def artifact_path(self, course_id: str, index_version: str) -> Path:
        course = _safe_path_component(course_id, name="course_id")
        version = _safe_path_component(index_version, name="index_version")
        return self.root / course / version / ARTIFACT_FILENAME

    def save(
        self,
        course_id: str,
        index_version: str,
        index: LightweightBM25Index,
    ) -> Path:
        target = self.artifact_path(course_id, index_version)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{ARTIFACT_FILENAME}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                json.dump(
                    index.to_payload(),
                    temporary,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_path, target)
            temporary_path = None
        except (OSError, TypeError, ValueError) as exc:
            raise LexicalIndexArtifactError("failed to save lexical artifact") from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        return target

    # ``replace`` is intentionally explicit for worker publication call sites.
    replace = save

    def load(self, course_id: str, index_version: str) -> LightweightBM25Index:
        target = self.artifact_path(course_id, index_version)
        try:
            with target.open(encoding="utf-8") as artifact:
                payload = json.load(artifact)
        except (OSError, json.JSONDecodeError) as exc:
            raise LexicalIndexArtifactError("failed to load lexical artifact") from exc
        if not isinstance(payload, dict):
            raise LexicalIndexArtifactError("lexical artifact root must be an object")
        return LightweightBM25Index.from_payload(payload)


class LexicalIndexManager:
    """Course-version lexical retriever with atomic disk and cache publication."""

    def __init__(self, root: str | Path) -> None:
        self.store = LexicalIndexArtifactStore(root)
        self._cache: dict[tuple[str, str], LightweightBM25Index] = {}
        self._lock = threading.RLock()

    def publish(
        self,
        course_id: str,
        index_version: str,
        documents: Iterable[LexicalDocument],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> Path:
        index = LightweightBM25Index(documents, k1=k1, b=b)
        return self.replace(course_id, index_version, index)

    def replace(
        self,
        course_id: str,
        index_version: str,
        index: LightweightBM25Index,
    ) -> Path:
        # Persist first. A failed write leaves the currently served object intact.
        path = self.store.replace(course_id, index_version, index)
        key = (str(course_id), str(index_version))
        with self._lock:
            self._cache[key] = index
        return path

    def get(self, course_id: str, index_version: str) -> LightweightBM25Index:
        key = (str(course_id), str(index_version))
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        loaded = self.store.load(course_id, index_version)
        with self._lock:
            return self._cache.setdefault(key, loaded)

    def evict(self, course_id: str, index_version: str) -> None:
        with self._lock:
            self._cache.pop((str(course_id), str(index_version)), None)

    def search(
        self,
        *,
        course_id: str,
        index_version: str,
        query: str,
        top_k: int = 20,
        filters: Mapping[str, Any] | None = None,
    ) -> list[LexicalCandidate]:
        # Course/version isolation is enforced by selecting exactly one artifact.
        # Metadata filters remain a boundary for a future bm25s-backed adapter.
        if filters:
            raise ValueError("metadata filters are not supported by this index")
        return self.get(course_id, index_version).search(query, top_k=top_k)
