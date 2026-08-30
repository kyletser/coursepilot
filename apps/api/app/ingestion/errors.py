from __future__ import annotations

from enum import StrEnum
from typing import Any


class IngestionErrorCode(StrEnum):
    """Stable machine-readable failures produced by the pure ingestion service."""

    UNSUPPORTED_DOCUMENT_FORMAT = "UNSUPPORTED_DOCUMENT_FORMAT"
    DOCUMENT_FORMAT_MISMATCH = "DOCUMENT_FORMAT_MISMATCH"
    DOCUMENT_TOO_LARGE = "DOCUMENT_TOO_LARGE"
    DOCUMENT_CORRUPTED = "DOCUMENT_CORRUPTED"
    PDF_PASSWORD_PROTECTED = "PDF_PASSWORD_PROTECTED"
    DOCUMENT_EMPTY = "DOCUMENT_EMPTY"
    DOCUMENT_UNSAFE_CONTENT = "DOCUMENT_UNSAFE_CONTENT"
    PARSER_DEPENDENCY_MISSING = "PARSER_DEPENDENCY_MISSING"


_MESSAGES: dict[IngestionErrorCode, str] = {
    IngestionErrorCode.UNSUPPORTED_DOCUMENT_FORMAT: (
        "Only PDF, DOCX, PPTX, Markdown, and TXT documents are supported"
    ),
    IngestionErrorCode.DOCUMENT_FORMAT_MISMATCH: (
        "The file content does not match its declared document format"
    ),
    IngestionErrorCode.DOCUMENT_TOO_LARGE: "The document exceeds the parsing limit",
    IngestionErrorCode.DOCUMENT_CORRUPTED: "The document is damaged or malformed",
    IngestionErrorCode.PDF_PASSWORD_PROTECTED: (
        "Password-protected PDF documents are not supported"
    ),
    IngestionErrorCode.DOCUMENT_EMPTY: (
        "The document contains no extractable text; scanned PDFs require OCR"
    ),
    IngestionErrorCode.DOCUMENT_UNSAFE_CONTENT: (
        "The document contains unsupported active or unsafe content"
    ),
    IngestionErrorCode.PARSER_DEPENDENCY_MISSING: (
        "A required document parser dependency is not installed"
    ),
}


class IngestionError(Exception):
    """Expected ingestion failure safe to persist on an IngestionJob."""

    def __init__(
        self,
        code: IngestionErrorCode,
        *,
        details: dict[str, Any] | None = None,
        message: str | None = None,
    ) -> None:
        self.code = code
        self.message = message or _MESSAGES[code]
        self.details = details or {}
        super().__init__(self.message)


def ingestion_error(
    code: IngestionErrorCode,
    **details: Any,
) -> IngestionError:
    return IngestionError(code, details=details)
