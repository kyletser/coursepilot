from __future__ import annotations

import uuid
from typing import Literal, Self

from fastapi import APIRouter, Request
from pydantic import Field, field_validator, model_validator

from app.dependencies import CurrentUser, SessionDep, TeacherUser
from app.errors import success_response
from app.graph.service import MAX_BATCH_REVIEW_ITEMS, BatchReviewItem, GraphService
from app.models import RelationType, ReviewStatus
from app.schemas import RequestModel

router = APIRouter(prefix="/api/v1", tags=["graph"])


class ConceptEditRequest(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    aliases: list[str] | None = Field(default=None, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be empty")
        return stripped

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        aliases: list[str] = []
        seen: set[str] = set()
        for raw_alias in value:
            alias = raw_alias.strip()
            if not alias:
                raise ValueError("aliases cannot contain empty values")
            if len(alias) > 255:
                raise ValueError("aliases cannot exceed 255 characters")
            normalized = alias.casefold()
            if normalized not in seen:
                seen.add(normalized)
                aliases.append(alias)
        return aliases

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("At least one editable field is required")
        for field_name in self.model_fields_set:
            if getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class ConceptApproveRequest(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be empty")
        return stripped

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class RelationApproveRequest(RequestModel):
    type: RelationType | None = None


class BatchReviewItemRequest(RequestModel):
    kind: Literal["concept", "relation"]
    candidate_id: uuid.UUID
    decision: Literal["approve", "reject"]
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    relation_type: RelationType | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("name cannot be empty")
        return stripped

    @field_validator("description")
    @classmethod
    def strip_description(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def scope_optional_overrides(self) -> Self:
        if (self.name is not None or self.description is not None) and not (
            self.kind == "concept" and self.decision == "approve"
        ):
            raise ValueError(
                "name and description are only allowed when approving a concept"
            )
        if self.relation_type is not None and not (
            self.kind == "relation" and self.decision == "approve"
        ):
            raise ValueError("relation_type is only allowed when approving a relation")
        return self

    def to_service_item(self) -> BatchReviewItem:
        return BatchReviewItem(
            kind=self.kind,
            candidate_id=self.candidate_id,
            decision=self.decision,
            name=self.name,
            description=self.description,
            relation_type=self.relation_type,
        )


class BatchReviewRequest(RequestModel):
    items: list[BatchReviewItemRequest] = Field(
        min_length=1, max_length=MAX_BATCH_REVIEW_ITEMS
    )

    @model_validator(mode="after")
    def require_unique_candidates(self) -> Self:
        seen: set[uuid.UUID] = set()
        for item in self.items:
            if item.candidate_id in seen:
                raise ValueError(
                    f"candidate_id {item.candidate_id} appears more than once"
                )
            seen.add(item.candidate_id)
        return self


@router.get("/courses/{course_id}/graph/candidates")
async def list_graph_candidates(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
    status: ReviewStatus | None = None,
):
    data = await GraphService(session).list_candidates(
        course_id, teacher, status=status
    )
    return success_response(request, data)


@router.post("/courses/{course_id}/graph/candidates/batch-review")
async def batch_review_graph_candidates(
    course_id: uuid.UUID,
    payload: BatchReviewRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    data = await GraphService(session).batch_review(
        course_id,
        teacher,
        [item.to_service_item() for item in payload.items],
    )
    return success_response(request, data)


@router.patch("/graph/concepts/{candidate_id}")
async def edit_graph_concept(
    candidate_id: uuid.UUID,
    payload: ConceptEditRequest,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    data = await GraphService(session).edit_concept(
        candidate_id,
        teacher,
        payload.model_dump(exclude_unset=True),
    )
    return success_response(request, data)


@router.post("/graph/concepts/{candidate_id}/approve")
async def approve_graph_concept(
    candidate_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
    payload: ConceptApproveRequest | None = None,
):
    data = await GraphService(session).approve_concept(
        candidate_id,
        teacher,
        name=payload.name if payload is not None else None,
        description=payload.description if payload is not None else None,
    )
    return success_response(request, data)


@router.post("/graph/concepts/{candidate_id}/reject")
async def reject_graph_concept(
    candidate_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    data = await GraphService(session).reject_concept(candidate_id, teacher)
    return success_response(request, data)


@router.post("/graph/relations/{candidate_id}/approve")
async def approve_graph_relation(
    candidate_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
    payload: RelationApproveRequest | None = None,
):
    data = await GraphService(session).approve_relation(
        candidate_id,
        teacher,
        relation_type=payload.type if payload is not None else None,
    )
    return success_response(request, data)


@router.post("/graph/relations/{candidate_id}/reject")
async def reject_graph_relation(
    candidate_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    data = await GraphService(session).reject_relation(candidate_id, teacher)
    return success_response(request, data)


@router.get("/courses/{course_id}/graph")
async def get_approved_graph(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    current_user: CurrentUser,
):
    data = await GraphService(session).approved_graph(course_id, current_user)
    return success_response(request, data)


@router.post("/courses/{course_id}/graph/publish")
async def publish_graph(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    data = await GraphService(session).publish(course_id, teacher)
    return success_response(request, data)
