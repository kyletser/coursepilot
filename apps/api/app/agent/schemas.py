from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


def _normalize_target_concepts(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = raw.strip()
        if not value:
            raise ValueError("target concepts must not be blank")
        if len(value) > 128:
            raise ValueError("target concepts must be at most 128 characters")
        key = value.casefold()
        if key not in seen:
            normalized.append(value)
            seen.add(key)
    return normalized


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Intent(StrEnum):
    TUTOR_QA = "TUTOR_QA"
    CONCEPT_COMPARE = "CONCEPT_COMPARE"
    DIAGNOSE = "DIAGNOSE"
    QUIZ = "QUIZ"
    LEARNING_PATH = "LEARNING_PATH"


class AgentRequest(StrictModel):
    course_id: NonBlank
    query: str = Field(min_length=1, max_length=4000)
    student_id: str | None = None
    requested_intent: Intent | None = None
    target_concepts: list[str] = Field(default_factory=list, max_length=16)
    active_index_version: str | None = None

    @field_validator(
        "course_id", "query", "student_id", "active_index_version", mode="before"
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("target_concepts")
    @classmethod
    def normalize_targets(cls, values: list[str]) -> list[str]:
        return _normalize_target_concepts(values)


class GuardResult(StrictModel):
    request: AgentRequest
    security_signals: list[str] = Field(default_factory=list)


class IntentRoute(StrictModel):
    intent: Intent
    target_concepts: list[str] = Field(default_factory=list, max_length=16)
    needs_retrieval: StrictBool
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("target_concepts")
    @classmethod
    def normalize_targets(cls, values: list[str]) -> list[str]:
        return _normalize_target_concepts(values)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("reason must not be blank")
        return stripped


class Evidence(StrictModel):
    course_id: NonBlank
    index_version: NonBlank
    chunk_id: NonBlank
    document: NonBlank
    document_version: NonBlank
    section: str | None = None
    page: StrictInt | None = Field(default=None, ge=1)
    text: str = Field(min_length=1, max_length=20000)
    # Normalized relevance supplied by the retrieval-to-Agent adapter. Raw RRF or
    # reranker logits must be calibrated before crossing this trust boundary.
    score: StrictFloat = Field(ge=0.0, le=1.0)

    @field_validator(
        "course_id",
        "index_version",
        "chunk_id",
        "document",
        "document_version",
        "section",
        "text",
        mode="before",
    )
    @classmethod
    def strip_strings(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("evidence string fields must not be blank")
        return stripped


class ClaimDraft(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    citation_labels: list[StrictInt] = Field(min_length=1, max_length=8)

    @field_validator("text")
    @classmethod
    def strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("claim text must not be blank")
        return stripped

    @field_validator("citation_labels")
    @classmethod
    def unique_positive_labels(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("citation labels start at 1")
        if len(set(values)) != len(values):
            raise ValueError("citation labels must be unique")
        return values


class AnswerDraft(StrictModel):
    """The only shape a generation adapter may return.

    There is deliberately no free-form ``answer`` field: the trusted core renders the
    final answer from individually cited claims so uncited prose cannot slip through.
    """

    claims: list[ClaimDraft] = Field(min_length=1, max_length=20)


class Citation(StrictModel):
    label: StrictInt = Field(ge=1)
    course_id: NonBlank
    index_version: NonBlank
    document: NonBlank
    document_version: NonBlank
    section: str | None = None
    page: StrictInt | None = Field(default=None, ge=1)
    chunk_id: NonBlank
    quote: str = Field(min_length=1, max_length=1200)
    claim_indices: list[StrictInt] = Field(default_factory=list)

    @field_validator("claim_indices")
    @classmethod
    def unique_non_negative_indices(cls, values: list[int]) -> list[int]:
        if any(value < 0 for value in values):
            raise ValueError("claim indices must be non-negative")
        if len(set(values)) != len(values):
            raise ValueError("claim indices must be unique")
        return values


class EvidenceGrade(StrictModel):
    sufficient: bool
    accepted: list[Evidence] = Field(default_factory=list)
    rejected_chunk_ids: list[str] = Field(default_factory=list)
    reason: str


class GroundingReport(StrictModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)


class BudgetSnapshot(StrictModel):
    query_rewrites: int
    tool_calls: int
    steps: int
    max_query_rewrites: int
    max_tool_calls: int
    max_steps: int


class AgentStatus(StrEnum):
    ANSWERED = "ANSWERED"
    EVIDENCE_SUMMARY = "EVIDENCE_SUMMARY"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    ROUTED = "ROUTED"
    RETRIEVAL_UNAVAILABLE = "RETRIEVAL_UNAVAILABLE"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class AgentResponse(StrictModel):
    status: AgentStatus
    route: IntentRoute
    answer: str
    claims: list[ClaimDraft] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    error_code: str | None = None
    error_message: str | None = None
    rewritten_query: str | None = None
    grounding: GroundingReport | None = None
    budget: BudgetSnapshot
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def error_fields_are_coherent(self) -> AgentResponse:
        if (self.error_code is None) != (self.error_message is None):
            raise ValueError("error_code and error_message must be set together")
        if self.status is AgentStatus.ANSWERED and (
            not self.claims or not self.citations or self.error_code is not None
        ):
            raise ValueError("answered responses require grounded claims and citations")
        if self.status is AgentStatus.INSUFFICIENT_EVIDENCE and (
            self.error_code != "INSUFFICIENT_EVIDENCE" or self.claims or self.citations
        ):
            raise ValueError("insufficient evidence responses must be pure refusals")
        return self
