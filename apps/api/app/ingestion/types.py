from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class DocumentFormat(StrEnum):
    PDF = "pdf"
    DOCX = "docx"
    PPTX = "pptx"
    MARKDOWN = "markdown"
    TXT = "txt"


class ChunkType(StrEnum):
    # Values intentionally match the persistence enum in app.models.
    TEXT = "TEXT"
    BODY = "TEXT"
    LIST = "LIST"
    TABLE = "TABLE"
    CODE_BLOCK = "CODE_BLOCK"
    CODE = "CODE_BLOCK"
    FORMULA = "FORMULA"
    EXAMPLE = "EXAMPLE"
    ANSWER = "ANSWER"
    LAB_STEP = "LAB_STEP"
    EXPERIMENT_STEP = "LAB_STEP"


@dataclass(frozen=True, slots=True)
class ParsedBlock:
    text: str
    chunk_type: ChunkType = ChunkType.TEXT
    page: int | None = None
    section_path: tuple[str, ...] = ()
    title_level: int | None = None


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    document_format: DocumentFormat
    blocks: tuple[ParsedBlock, ...]


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    chunk_id: UUID
    ordinal: int
    content: str
    embedding_text: str
    chunk_type: ChunkType
    page: int | None
    section_path: tuple[str, ...]
    title_level: int | None
    previous_chunk_id: UUID | None
    next_chunk_id: UUID | None

    @property
    def character_count(self) -> int:
        return len(self.content)

    @property
    def char_count(self) -> int:
        return len(self.content)
