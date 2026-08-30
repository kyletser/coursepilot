from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.dependencies import SessionDep, StudentUser, TeacherUser
from app.errors import success_response
from app.learning.history import student_history_service
from app.learning.service import (
    AttemptOutcome,
    LearningPathResult,
    QuizCandidateDraft,
    learning_service,
)
from app.models import (
    ConceptCandidate,
    MasteryState,
    QuizAttemptStatus,
    QuizDifficulty,
    ReviewStatus,
)

router = APIRouter(prefix="/api/v1", tags=["learning"])


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QuizCandidateInput(RequestModel):
    concept_id: uuid.UUID
    source_chunk_id: uuid.UUID
    question: str = Field(min_length=1, max_length=10_000)
    options: list[str] = Field(min_length=2, max_length=8)
    answer: str = Field(min_length=1, max_length=4_000)
    explanation: str = Field(min_length=1, max_length=20_000)
    difficulty: QuizDifficulty

    @field_validator("question", "answer", "explanation")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("options")
    @classmethod
    def validate_options(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("quiz options must not be blank")
        if len(set(normalized)) != len(normalized):
            raise ValueError("quiz options must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_unique_answer(self) -> QuizCandidateInput:
        if sum(option == self.answer for option in self.options) != 1:
            raise ValueError("answer must match exactly one quiz option")
        return self

    def to_draft(self) -> QuizCandidateDraft:
        return QuizCandidateDraft(
            concept_id=self.concept_id,
            source_chunk_id=self.source_chunk_id,
            question=self.question,
            options=tuple(self.options),
            answer=self.answer,
            explanation=self.explanation,
            difficulty=self.difficulty,
        )


class GenerateQuizCandidatesRequest(RequestModel):
    items: list[QuizCandidateInput] = Field(default_factory=list, max_length=20)


class QuizReviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    concept_id: uuid.UUID
    source_chunk_id: uuid.UUID
    question: str
    options: list[str]
    answer: str
    explanation: str
    difficulty: QuizDifficulty
    status: ReviewStatus
    generation_model: str
    reviewed_by: uuid.UUID | None
    reviewed_at: datetime | None
    created_at: datetime


class StudentQuizResponse(BaseModel):
    """Student-facing shape deliberately excludes answer and explanation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    course_id: uuid.UUID
    concept_id: uuid.UUID
    question: str
    options: list[str]
    difficulty: QuizDifficulty


class QuizAttemptRequest(RequestModel):
    answer: str = Field(min_length=1, max_length=4_000)
    idempotency_key: str = Field(min_length=1, max_length=255)

    @field_validator("answer", "idempotency_key")
    @classmethod
    def strip_nonempty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class MasteryResponse(BaseModel):
    id: uuid.UUID
    course_id: uuid.UUID
    concept_id: uuid.UUID
    concept_name: str
    alpha: float
    beta: float
    mastery: float
    attempt_count: int
    last_assessed_at: datetime | None

    @classmethod
    def from_row(
        cls, state: MasteryState, concept: ConceptCandidate
    ) -> MasteryResponse:
        return cls.from_state(state, concept_name=concept.name)

    @classmethod
    def from_state(
        cls, state: MasteryState, *, concept_name: str = ""
    ) -> MasteryResponse:
        return cls(
            id=state.id,
            course_id=state.course_id,
            concept_id=state.concept_id,
            concept_name=concept_name,
            alpha=state.alpha,
            beta=state.beta,
            mastery=state.mastery,
            attempt_count=state.attempt_count,
            last_assessed_at=state.last_assessed_at,
        )


class QuizAttemptResponse(BaseModel):
    id: uuid.UUID
    quiz_item_id: uuid.UUID
    answer: str
    correct: bool
    correct_answer: str
    explanation: str
    weight: float
    status: QuizAttemptStatus
    submitted_at: datetime
    graded_at: datetime | None
    replayed: bool
    mastery: MasteryResponse

    @classmethod
    def from_outcome(cls, result: AttemptOutcome) -> QuizAttemptResponse:
        mastery = MasteryResponse.from_state(result.mastery)
        return cls(
            id=result.attempt.id,
            quiz_item_id=result.attempt.quiz_item_id,
            answer=result.attempt.answer,
            correct=result.attempt.correct,
            correct_answer=result.quiz.answer,
            explanation=result.quiz.explanation,
            weight=result.attempt.weight,
            status=result.attempt.status,
            submitted_at=result.attempt.submitted_at,
            graded_at=result.attempt.graded_at,
            replayed=result.replayed,
            mastery=mastery,
        )


class LearningPathRequest(RequestModel):
    target_concept_id: uuid.UUID
    max_depth: int = Field(default=4, ge=1, le=4)


class LearningPathStepResponse(BaseModel):
    concept_id: uuid.UUID
    concept_name: str
    mastery: float
    is_weak: bool
    depth: int
    reason: str
    related_material_chunk_ids: list[uuid.UUID]
    optional_quiz_item_id: uuid.UUID | None


class LearningPathResponse(BaseModel):
    target_concept_id: uuid.UUID
    max_depth: int
    steps: list[LearningPathStepResponse]

    @classmethod
    def from_result(cls, result: LearningPathResult) -> LearningPathResponse:
        return cls(
            target_concept_id=result.target_concept_id,
            max_depth=result.max_depth,
            steps=[
                LearningPathStepResponse(
                    concept_id=step.concept.id,
                    concept_name=step.concept.name,
                    mastery=step.mastery,
                    is_weak=step.is_weak,
                    depth=step.depth,
                    reason=step.reason,
                    related_material_chunk_ids=[step.concept.source_chunk_id],
                    optional_quiz_item_id=step.quiz_item_id,
                )
                for step in result.steps
            ],
        )


@router.post(
    "/courses/{course_id}/quizzes/generate-candidates",
    status_code=201,
)
async def generate_quiz_candidates(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
    payload: GenerateQuizCandidatesRequest | None = None,
):
    candidate_inputs = payload.items if payload is not None else ()
    items = await learning_service.generate_candidates(
        session,
        course_id,
        teacher,
        tuple(item.to_draft() for item in candidate_inputs),
    )
    return success_response(
        request,
        [QuizReviewResponse.model_validate(item) for item in items],
        status_code=201,
    )


@router.get("/courses/{course_id}/quizzes/review")
async def list_quiz_review_queue(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
    status: Annotated[ReviewStatus | None, Query()] = ReviewStatus.PENDING,
):
    items = await learning_service.list_review_items(
        session,
        course_id,
        teacher,
        status=status,
    )
    return success_response(
        request,
        [QuizReviewResponse.model_validate(item) for item in items],
    )


@router.post("/quizzes/{item_id}/approve")
async def approve_quiz_item(
    item_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    item = await learning_service.review_item(
        session,
        item_id,
        teacher,
        ReviewStatus.APPROVED,
    )
    return success_response(request, QuizReviewResponse.model_validate(item))


@router.post("/quizzes/{item_id}/reject")
async def reject_quiz_item(
    item_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    teacher: TeacherUser,
):
    item = await learning_service.review_item(
        session,
        item_id,
        teacher,
        ReviewStatus.REJECTED,
    )
    return success_response(request, QuizReviewResponse.model_validate(item))


@router.get("/courses/{course_id}/quizzes/next")
async def get_next_quiz_item(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    student: StudentUser,
    concept_id: uuid.UUID | None = None,
    difficulty: QuizDifficulty | None = None,
):
    item = await learning_service.next_quiz(
        session,
        course_id,
        student,
        concept_id=concept_id,
        difficulty=difficulty,
    )
    return success_response(request, StudentQuizResponse.model_validate(item))


@router.post("/quizzes/{item_id}/attempts")
async def submit_quiz_attempt(
    item_id: uuid.UUID,
    payload: QuizAttemptRequest,
    request: Request,
    session: SessionDep,
    student: StudentUser,
):
    result = await learning_service.submit_attempt(
        session,
        item_id,
        student,
        answer=payload.answer,
        idempotency_key=payload.idempotency_key,
    )
    return success_response(request, QuizAttemptResponse.from_outcome(result))


@router.get("/courses/{course_id}/mastery")
async def get_mastery(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    student: StudentUser,
):
    rows = await learning_service.list_mastery(session, course_id, student)
    return success_response(
        request,
        [MasteryResponse.from_row(state, concept) for state, concept in rows],
    )


@router.get("/courses/{course_id}/learning-history")
async def get_learning_history(
    course_id: uuid.UUID,
    request: Request,
    session: SessionDep,
    student: StudentUser,
):
    history = await student_history_service.get_learning_history(
        session, course_id, student
    )
    return success_response(request, history)


@router.post("/courses/{course_id}/learning-path")
async def create_learning_path(
    course_id: uuid.UUID,
    payload: LearningPathRequest,
    request: Request,
    session: SessionDep,
    student: StudentUser,
):
    result = await learning_service.learning_path(
        session,
        course_id,
        student,
        target_concept_id=payload.target_concept_id,
        max_depth=payload.max_depth,
    )
    return success_response(request, LearningPathResponse.from_result(result))
