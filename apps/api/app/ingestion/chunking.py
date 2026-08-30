from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from uuid import UUID

from app.ingestion.types import ChunkDraft, ChunkType, ParsedBlock, ParsedDocument

_CHUNK_NAMESPACE = uuid.uuid5(
    uuid.NAMESPACE_URL, "https://coursepilot.local/ingestion/chunks"
)
_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[。！？!?；;])|\n+|\s+")


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    target_min: int = 300
    target_max: int = 600
    hard_max: int = 900

    def __post_init__(self) -> None:
        if not (0 < self.target_min <= self.target_max <= self.hard_max):
            raise ValueError(
                "Chunk sizes must satisfy 0 < target_min <= target_max <= hard_max"
            )


@dataclass(frozen=True, slots=True)
class _Piece:
    content: str
    chunk_type: ChunkType
    page: int | None
    section_path: tuple[str, ...]
    title_level: int | None

    @property
    def key(self) -> tuple[object, ...]:
        return (
            self.page,
            self.section_path,
            self.title_level,
            self.chunk_type,
        )


class StructureAwareChunker:
    """Deterministic, metadata-preserving chunker for parsed course documents."""

    def __init__(self, config: ChunkingConfig | None = None) -> None:
        self.config = config or ChunkingConfig()

    def chunk(
        self,
        document: ParsedDocument,
        *,
        document_key: str | UUID | None = None,
    ) -> list[ChunkDraft]:
        pieces = self._build_pieces(document.blocks)
        if not pieces:
            return []

        stable_key = (
            str(document_key) if document_key is not None else _fingerprint(document)
        )
        chunk_ids = [
            uuid.uuid5(_CHUNK_NAMESPACE, f"{stable_key}:{ordinal}")
            for ordinal in range(len(pieces))
        ]
        chunks: list[ChunkDraft] = []
        for ordinal, piece in enumerate(pieces):
            section_prefix = " > ".join(piece.section_path)
            embedding_text = (
                f"{section_prefix}\n\n{piece.content}"
                if section_prefix
                else piece.content
            )
            chunks.append(
                ChunkDraft(
                    chunk_id=chunk_ids[ordinal],
                    ordinal=ordinal,
                    content=piece.content,
                    embedding_text=embedding_text,
                    chunk_type=piece.chunk_type,
                    page=piece.page,
                    section_path=piece.section_path,
                    title_level=piece.title_level,
                    previous_chunk_id=chunk_ids[ordinal - 1] if ordinal else None,
                    next_chunk_id=(
                        chunk_ids[ordinal + 1] if ordinal + 1 < len(chunk_ids) else None
                    ),
                )
            )
        return chunks

    def _build_pieces(self, blocks: tuple[ParsedBlock, ...]) -> list[_Piece]:
        pieces: list[_Piece] = []
        regular_blocks: list[ParsedBlock] = []
        regular_key: tuple[object, ...] | None = None

        def flush_regular() -> None:
            nonlocal regular_blocks, regular_key
            if not regular_blocks:
                return
            first = regular_blocks[0]
            combined = "\n\n".join(
                normalized
                for block in regular_blocks
                if (normalized := _normalize_block_text(block.text))
            )
            for content in _split_regular_text(combined, self.config):
                pieces.append(_piece_for(first, content))
            regular_blocks = []
            regular_key = None

        for block in blocks:
            normalized = _normalize_block_text(block.text)
            if not normalized:
                continue
            normalized_block = ParsedBlock(
                text=normalized,
                chunk_type=block.chunk_type,
                page=block.page,
                section_path=block.section_path,
                title_level=block.title_level,
            )
            if block.chunk_type in {ChunkType.CODE_BLOCK, ChunkType.TABLE}:
                flush_regular()
                for content in _split_atomic_text(normalized, self.config.hard_max):
                    pieces.append(_piece_for(normalized_block, content))
                continue

            key = (
                block.page,
                block.section_path,
                block.title_level,
                block.chunk_type,
            )
            if regular_key is not None and key != regular_key:
                flush_regular()
            regular_key = key
            regular_blocks.append(normalized_block)
        flush_regular()
        return pieces


def chunk_document(
    document: ParsedDocument,
    *,
    document_key: str | UUID | None = None,
    config: ChunkingConfig | None = None,
) -> list[ChunkDraft]:
    return StructureAwareChunker(config).chunk(document, document_key=document_key)


def _piece_for(block: ParsedBlock, content: str) -> _Piece:
    return _Piece(
        content=content,
        chunk_type=block.chunk_type,
        page=block.page,
        section_path=block.section_path,
        title_level=block.title_level,
    )


def _normalize_block_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _split_regular_text(text: str, config: ChunkingConfig) -> list[str]:
    if not text:
        return []
    if len(text) <= config.target_max:
        return [text]

    chunks: list[str] = []
    start = 0
    text_length = len(text)
    while text_length - start > config.target_max:
        remaining = text_length - start
        desired = config.target_max
        if remaining - desired < config.target_min:
            desired = remaining // 2

        lower = start + min(config.target_min, desired)
        upper = start + desired
        cut = _last_semantic_boundary(text, lower, upper)
        if cut is None:
            cut = upper
        content = text[start:cut].strip()
        if content:
            chunks.append(content)
        start = cut
        while start < text_length and text[start].isspace():
            start += 1

    tail = text[start:].strip()
    if tail:
        chunks.append(tail)
    return _enforce_hard_max(chunks, config.hard_max)


def _last_semantic_boundary(text: str, lower: int, upper: int) -> int | None:
    candidate: int | None = None
    for match in _SENTENCE_BOUNDARY_RE.finditer(text, lower, upper):
        boundary = match.end()
        if lower <= boundary <= upper:
            candidate = boundary
    return candidate


def _split_atomic_text(text: str, hard_max: int) -> list[str]:
    if len(text) <= hard_max:
        return [text]

    chunks: list[str] = []
    start = 0
    while len(text) - start > hard_max:
        upper = start + hard_max
        newline = text.rfind("\n", start + 1, upper + 1)
        cut = newline + 1 if newline > start else upper
        content = text[start:cut].strip("\n")
        if content:
            chunks.append(content)
        start = cut
    tail = text[start:].strip("\n")
    if tail:
        chunks.append(tail)
    return _enforce_hard_max(chunks, hard_max)


def _enforce_hard_max(chunks: list[str], hard_max: int) -> list[str]:
    safe: list[str] = []
    for chunk in chunks:
        if len(chunk) <= hard_max:
            safe.append(chunk)
            continue
        safe.extend(
            chunk[offset : offset + hard_max]
            for offset in range(0, len(chunk), hard_max)
        )
    return safe


def _fingerprint(document: ParsedDocument) -> str:
    digest = hashlib.sha256(document.document_format.value.encode("ascii"))
    for block in document.blocks:
        digest.update(b"\x00")
        digest.update(block.text.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(block.chunk_type.value.encode("ascii"))
        digest.update(b"\x00")
        digest.update(str(block.page).encode("ascii"))
        digest.update(b"\x00")
        digest.update("\x1f".join(block.section_path).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(block.title_level).encode("ascii"))
    return digest.hexdigest()
