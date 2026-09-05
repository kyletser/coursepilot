from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import case as sql_case
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import AppError
from app.evaluation import (
    AbstentionCase,
    CitationCase,
    CitationJudgment,
    EvalConfig,
    PathCase,
    RetrievalCase,
    RoutingCase,
    evaluate_abstention,
    evaluate_citations,
    evaluate_paths,
    evaluate_retrieval,
    evaluate_routing,
)
from app.evaluation import (
    EvalDataset as DomainEvalDataset,
)
from app.models import (
    BadCase,
    BadCaseSourceType,
    BadCaseStatus,
    ChatSession,
    ConceptCandidate,
    Course,
    CourseIndex,
    CourseIndexStatus,
    Enrollment,
    EnrollmentStatus,
    EvalCase,
    EvalDataset,
    EvalDatasetStatus,
    EvalDatasetType,
    EvalRun,
    EvalRunStatus,
    MasteryState,
    Message,
    QuizAttempt,
    QuizAttemptStatus,
    QuizItem,
    RecordStatus,
    ReviewStatus,
    User,
    UserRole,
)

RUN_CONFIG_SCHEMA_VERSION = "coursepilot.eval-run-config/1.0.0"
METRICS_SCHEMA_VERSION = "coursepilot.eval-metrics/1.0.0"


def _invalid_result(
    message: str, *, case_key: str | None = None, field: str | None = None
) -> AppError:
    details: dict[str, str] = {}
    if case_key is not None:
        details["case_key"] = case_key
    if field is not None:
        details["field"] = field
    return AppError(
        422,
        "EVAL_RESULT_INVALID",
        message,
        details=details,
    )


def _json_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise _invalid_result("Evaluation data must be valid JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def _mapping(value: object, *, case_key: str, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _invalid_result(
            f"{field} must be a JSON object", case_key=case_key, field=field
        )
    return value


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _string_sequence(
    value: Any,
    *,
    case_key: str,
    field: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or (not value and not allow_empty)
    ):
        raise _invalid_result(
            f"{field} must be a non-empty array of strings",
            case_key=case_key,
            field=field,
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _invalid_result(
                f"{field} must contain non-blank strings",
                case_key=case_key,
                field=field,
            )
        result.append(item.strip())
    return tuple(result)


def _bool(value: Any, *, case_key: str, field: str) -> bool:
    if not isinstance(value, bool):
        raise _invalid_result(
            f"{field} must be a boolean", case_key=case_key, field=field
        )
    return value


async def require_owned_course(
    session: AsyncSession, course_id: uuid.UUID, teacher: User
) -> Course:
    course = await session.scalar(select(Course).where(Course.id == course_id))
    if course is None:
        raise AppError(404, "COURSE_NOT_FOUND", "Course not found")
    if teacher.role != UserRole.TEACHER or course.owner_id != teacher.id:
        raise AppError(
            403,
            "COURSE_ACCESS_DENIED",
            "You do not have access to this course",
        )
    return course


async def _owned_dataset(
    session: AsyncSession,
    dataset_id: uuid.UUID,
    teacher: User,
    *,
    for_update: bool = False,
) -> EvalDataset:
    statement = select(EvalDataset).where(
        EvalDataset.id == dataset_id, EvalDataset.deleted_at.is_(None)
    )
    if for_update:
        statement = statement.with_for_update()
    dataset = await session.scalar(statement)
    if dataset is None:
        raise AppError(404, "EVAL_DATASET_NOT_FOUND", "Evaluation dataset not found")
    await require_owned_course(session, dataset.course_id, teacher)
    return dataset


async def _active_cases(session: AsyncSession, dataset_id: uuid.UUID) -> list[EvalCase]:
    return list(
        (
            await session.scalars(
                select(EvalCase)
                .where(
                    EvalCase.dataset_id == dataset_id,
                    EvalCase.status == RecordStatus.ACTIVE,
                    EvalCase.deleted_at.is_(None),
                )
                .order_by(EvalCase.case_key.asc())
            )
        ).all()
    )


def _domain_snapshot(
    dataset: EvalDataset,
    cases: Sequence[EvalCase],
    *,
    frozen_at: datetime,
):
    domain = DomainEvalDataset(
        dataset_id=str(dataset.id),
        dataset_type=dataset.type.value,
        version=str(dataset.version),
        metadata={
            "course_id": str(dataset.course_id),
            "name": dataset.name,
            "description": dataset.description,
        },
    )
    for case in cases:
        domain.add_case(
            case.case_key,
            {
                "input": case.input,
                "expected": case.expected,
                "labels": case.labels,
            },
        )
    return domain.freeze(frozen_at=frozen_at)


async def dataset_case_count(session: AsyncSession, dataset_id: uuid.UUID) -> int:
    count = await session.scalar(
        select(func.count(EvalCase.id)).where(
            EvalCase.dataset_id == dataset_id,
            EvalCase.status == RecordStatus.ACTIVE,
            EvalCase.deleted_at.is_(None),
        )
    )
    return int(count or 0)


def dataset_data(
    dataset: EvalDataset,
    *,
    case_count: int,
    content_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "id": dataset.id,
        "course_id": dataset.course_id,
        "name": dataset.name,
        "description": dataset.description,
        "type": dataset.type,
        "version": dataset.version,
        "status": dataset.status,
        "created_by": dataset.created_by,
        "frozen_at": dataset.frozen_at,
        "case_count": case_count,
        "content_sha256": content_sha256,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


def case_data(case: EvalCase) -> dict[str, Any]:
    return {
        "id": case.id,
        "dataset_id": case.dataset_id,
        "case_key": case.case_key,
        "input": case.input,
        "expected": case.expected,
        "labels": case.labels,
        "status": case.status,
        "created_at": case.created_at,
        "updated_at": case.updated_at,
    }


def run_data(run: EvalRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "dataset_id": run.dataset_id,
        "config": run.config,
        "status": run.status,
        "metrics": run.metrics,
        "trace_id": run.trace_id,
        "git_commit": run.git_commit,
        "index_version": run.index_version,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def bad_case_data(bad_case: BadCase) -> dict[str, Any]:
    return {
        "id": bad_case.id,
        "course_id": bad_case.course_id,
        "source_type": bad_case.source_type,
        "source_id": bad_case.source_id,
        "category": bad_case.category,
        "status": bad_case.status,
        "notes": bad_case.notes,
        "reported_by": bad_case.reported_by,
        "resolved_at": bad_case.resolved_at,
        "created_at": bad_case.created_at,
        "updated_at": bad_case.updated_at,
    }


def run_list_data(run: EvalRun, dataset: EvalDataset) -> dict[str, Any]:
    return {
        **run_data(run),
        "dataset_name": dataset.name,
        "dataset_type": dataset.type,
        "dataset_version": dataset.version,
    }


async def list_bad_cases(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    teacher: User,
    status: BadCaseStatus | None,
    source_type: BadCaseSourceType | None,
    category: str | None,
    limit: int,
    offset: int,
) -> tuple[list[BadCase], int]:
    await require_owned_course(session, course_id, teacher)
    filters = [
        BadCase.course_id == course_id,
        BadCase.deleted_at.is_(None),
    ]
    if status is not None:
        filters.append(BadCase.status == status)
    if source_type is not None:
        filters.append(BadCase.source_type == source_type)
    if category is not None:
        filters.append(BadCase.category == category)

    total = await session.scalar(select(func.count(BadCase.id)).where(*filters))
    items = list(
        (
            await session.scalars(
                select(BadCase)
                .where(*filters)
                .order_by(BadCase.created_at.desc(), BadCase.id.desc())
                .limit(limit)
                .offset(offset)
            )
        ).all()
    )
    return items, int(total or 0)


async def list_eval_runs(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    teacher: User,
    dataset_id: uuid.UUID | None,
    status: EvalRunStatus | None,
    limit: int,
    offset: int,
) -> tuple[list[tuple[EvalRun, EvalDataset]], int]:
    await require_owned_course(session, course_id, teacher)
    filters = [
        EvalDataset.course_id == course_id,
        EvalDataset.deleted_at.is_(None),
        EvalRun.deleted_at.is_(None),
    ]
    if dataset_id is not None:
        filters.append(EvalRun.dataset_id == dataset_id)
    if status is not None:
        filters.append(EvalRun.status == status)

    total = await session.scalar(
        select(func.count(EvalRun.id))
        .join(EvalDataset, EvalDataset.id == EvalRun.dataset_id)
        .where(*filters)
    )
    rows = (
        await session.execute(
            select(EvalRun, EvalDataset)
            .join(EvalDataset, EvalDataset.id == EvalRun.dataset_id)
            .where(*filters)
            .order_by(EvalRun.created_at.desc(), EvalRun.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return [(run, dataset) for run, dataset in rows], int(total or 0)


async def learning_summary(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    teacher: User,
    weak_threshold: float = 0.60,
) -> dict[str, list[dict[str, Any]]]:
    await require_owned_course(session, course_id, teacher)
    enrollment_rows = (
        await session.execute(
            select(User, Enrollment)
            .join(Enrollment, Enrollment.student_id == User.id)
            .where(
                Enrollment.course_id == course_id,
                Enrollment.status == EnrollmentStatus.ACTIVE,
                User.role == UserRole.STUDENT,
            )
            .order_by(Enrollment.joined_at.asc(), User.id.asc())
        )
    ).all()
    if not enrollment_rows:
        return {"students": [], "weak_concepts": []}

    student_ids = [student.id for student, _ in enrollment_rows]
    correct_count = func.sum(sql_case((QuizAttempt.correct.is_(True), 1), else_=0))
    attempt_rows = (
        await session.execute(
            select(
                QuizAttempt.student_id,
                func.count(QuizAttempt.id),
                correct_count,
            )
            .join(QuizItem, QuizItem.id == QuizAttempt.quiz_item_id)
            .where(
                QuizItem.course_id == course_id,
                QuizItem.status == ReviewStatus.APPROVED,
                QuizItem.deleted_at.is_(None),
                QuizAttempt.student_id.in_(student_ids),
                QuizAttempt.status == QuizAttemptStatus.GRADED,
                QuizAttempt.deleted_at.is_(None),
            )
            .group_by(QuizAttempt.student_id)
        )
    ).all()
    attempts_by_student = {
        student_id: (int(attempt_count), int(correct or 0))
        for student_id, attempt_count, correct in attempt_rows
    }

    weak_count = func.sum(sql_case((MasteryState.mastery < weak_threshold, 1), else_=0))
    mastery_filters = [
        MasteryState.course_id == course_id,
        MasteryState.student_id.in_(student_ids),
        MasteryState.status == RecordStatus.ACTIVE,
        MasteryState.deleted_at.is_(None),
        ConceptCandidate.course_id == course_id,
        ConceptCandidate.status == ReviewStatus.APPROVED,
        ConceptCandidate.deleted_at.is_(None),
    ]
    mastery_rows = (
        await session.execute(
            select(
                MasteryState.student_id,
                func.count(MasteryState.id),
                func.avg(MasteryState.mastery),
                weak_count,
                func.max(MasteryState.last_assessed_at),
            )
            .join(ConceptCandidate, ConceptCandidate.id == MasteryState.concept_id)
            .where(*mastery_filters)
            .group_by(MasteryState.student_id)
        )
    ).all()
    mastery_by_student = {
        student_id: (
            int(concept_count),
            float(average_mastery),
            int(student_weak_count or 0),
            last_assessed_at,
        )
        for (
            student_id,
            concept_count,
            average_mastery,
            student_weak_count,
            last_assessed_at,
        ) in mastery_rows
    }

    students: list[dict[str, Any]] = []
    for student, enrollment in enrollment_rows:
        attempt_count, quiz_correct_count = attempts_by_student.get(student.id, (0, 0))
        mastery = mastery_by_student.get(student.id)
        students.append(
            {
                "id": student.id,
                "email": student.email,
                "user_status": student.status,
                "enrollment_id": enrollment.id,
                "enrollment_status": enrollment.status,
                "joined_at": enrollment.joined_at,
                "quiz_attempt_count": attempt_count,
                "quiz_correct_count": quiz_correct_count,
                "mastery_concept_count": mastery[0] if mastery else 0,
                "average_mastery": mastery[1] if mastery else None,
                "weak_concept_count": mastery[2] if mastery else 0,
                "last_assessed_at": mastery[3] if mastery else None,
            }
        )

    weak_student_count = func.sum(
        sql_case((MasteryState.mastery < weak_threshold, 1), else_=0)
    )
    weak_rows = (
        await session.execute(
            select(
                ConceptCandidate.id,
                ConceptCandidate.name,
                func.count(func.distinct(MasteryState.student_id)),
                weak_student_count,
                func.avg(MasteryState.mastery),
            )
            .join(MasteryState, MasteryState.concept_id == ConceptCandidate.id)
            .join(
                Enrollment,
                (Enrollment.student_id == MasteryState.student_id)
                & (Enrollment.course_id == course_id),
            )
            .where(
                *mastery_filters,
                Enrollment.status == EnrollmentStatus.ACTIVE,
            )
            .group_by(ConceptCandidate.id, ConceptCandidate.name)
            .having(weak_student_count > 0)
            .order_by(
                weak_student_count.desc(),
                func.avg(MasteryState.mastery).asc(),
                ConceptCandidate.name.asc(),
            )
        )
    ).all()
    weak_concepts = [
        {
            "concept_id": concept_id,
            "concept_name": concept_name,
            "assessed_student_count": int(assessed_student_count),
            "weak_student_count": int(concept_weak_count or 0),
            "average_mastery": float(average_mastery),
        }
        for (
            concept_id,
            concept_name,
            assessed_student_count,
            concept_weak_count,
            average_mastery,
        ) in weak_rows
    ]
    return {"students": students, "weak_concepts": weak_concepts}


async def create_dataset(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    teacher: User,
    name: str,
    description: str,
    dataset_type: EvalDatasetType,
    version: int | None,
) -> EvalDataset:
    await require_owned_course(session, course_id, teacher)
    selected_version = version
    if selected_version is None:
        latest = await session.scalar(
            select(func.max(EvalDataset.version)).where(
                EvalDataset.course_id == course_id,
                EvalDataset.type == dataset_type,
            )
        )
        selected_version = int(latest or 0) + 1
    dataset = EvalDataset(
        course_id=course_id,
        name=name,
        description=description,
        type=dataset_type,
        version=selected_version,
        created_by=teacher.id,
    )
    session.add(dataset)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            409,
            "EVAL_DATASET_VERSION_EXISTS",
            "This evaluation dataset version already exists",
        ) from exc
    await session.refresh(dataset)
    return dataset


async def list_datasets(
    session: AsyncSession, *, course_id: uuid.UUID, teacher: User
) -> list[EvalDataset]:
    await require_owned_course(session, course_id, teacher)
    return list(
        (
            await session.scalars(
                select(EvalDataset)
                .where(
                    EvalDataset.course_id == course_id,
                    EvalDataset.deleted_at.is_(None),
                )
                .order_by(
                    EvalDataset.type.asc(),
                    EvalDataset.version.desc(),
                    EvalDataset.created_at.desc(),
                )
            )
        ).all()
    )


async def get_dataset(
    session: AsyncSession, *, dataset_id: uuid.UUID, teacher: User
) -> tuple[EvalDataset, int, str | None]:
    dataset = await _owned_dataset(session, dataset_id, teacher)
    cases = await _active_cases(session, dataset.id)
    digest = None
    if dataset.status == EvalDatasetStatus.FROZEN and dataset.frozen_at is not None:
        digest = _domain_snapshot(
            dataset, cases, frozen_at=dataset.frozen_at
        ).content_sha256
    return dataset, len(cases), digest


async def add_case(
    session: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    teacher: User,
    case_key: str,
    case_input: dict[str, Any],
    expected: dict[str, Any],
    labels: dict[str, Any],
) -> EvalCase:
    dataset = await _owned_dataset(session, dataset_id, teacher, for_update=True)
    if dataset.status != EvalDatasetStatus.DRAFT:
        raise AppError(
            409,
            "EVAL_DATASET_FROZEN",
            "Frozen evaluation datasets are immutable",
        )
    _json_digest({"input": case_input, "expected": expected, "labels": labels})
    case = EvalCase(
        dataset_id=dataset.id,
        case_key=case_key,
        input=case_input,
        expected=expected,
        labels=labels,
    )
    session.add(case)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            409,
            "EVAL_CASE_KEY_EXISTS",
            "This case key already exists in the evaluation dataset",
        ) from exc
    await session.refresh(case)
    return case


async def list_cases(
    session: AsyncSession, *, dataset_id: uuid.UUID, teacher: User
) -> list[EvalCase]:
    dataset = await _owned_dataset(session, dataset_id, teacher)
    return await _active_cases(session, dataset.id)


async def update_case(
    session: AsyncSession,
    *,
    case_id: uuid.UUID,
    teacher: User,
    changes: Mapping[str, Any],
) -> EvalCase:
    case = await session.scalar(
        select(EvalCase)
        .where(EvalCase.id == case_id, EvalCase.deleted_at.is_(None))
        .with_for_update()
    )
    if case is None:
        raise AppError(404, "EVAL_CASE_NOT_FOUND", "Evaluation case not found")
    dataset = await _owned_dataset(session, case.dataset_id, teacher, for_update=True)
    if dataset.status != EvalDatasetStatus.DRAFT:
        raise AppError(
            409,
            "EVAL_DATASET_FROZEN",
            "Frozen evaluation datasets are immutable",
        )
    candidate = {
        "input": changes.get("input", case.input),
        "expected": changes.get("expected", case.expected),
        "labels": changes.get("labels", case.labels),
    }
    _json_digest(candidate)
    for field, value in changes.items():
        setattr(case, field, value)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            409,
            "EVAL_CASE_KEY_EXISTS",
            "This case key already exists in the evaluation dataset",
        ) from exc
    await session.refresh(case)
    return case


async def freeze_dataset(
    session: AsyncSession, *, dataset_id: uuid.UUID, teacher: User
) -> tuple[EvalDataset, int, str]:
    dataset = await _owned_dataset(session, dataset_id, teacher, for_update=True)
    cases = await _active_cases(session, dataset.id)
    if not cases:
        raise AppError(
            409,
            "EVAL_DATASET_EMPTY",
            "An empty evaluation dataset cannot be frozen",
        )
    frozen_at = dataset.frozen_at or datetime.now(UTC)
    snapshot = _domain_snapshot(dataset, cases, frozen_at=frozen_at)
    if dataset.status == EvalDatasetStatus.DRAFT:
        dataset.status = EvalDatasetStatus.FROZEN
        dataset.frozen_at = frozen_at
        await session.commit()
        await session.refresh(dataset)
    elif dataset.status != EvalDatasetStatus.FROZEN:
        raise AppError(
            409,
            "EVAL_DATASET_IMMUTABLE",
            "Only draft evaluation datasets can be frozen",
        )
    return dataset, len(cases), snapshot.content_sha256


def _case_output(case: EvalCase, case_results: Mapping[str, Any]) -> Mapping[str, Any]:
    supplied = case_results.get(case.case_key)
    if supplied is not None:
        return _mapping(supplied, case_key=case.case_key, field="case_results")
    embedded = _first(case.input, "observed", "output", "result", "actual")
    if embedded is not None:
        return _mapping(embedded, case_key=case.case_key, field="input.output")
    # This fallback supports compact frozen fixtures that place deterministic
    # observations beside the query in the input object.
    return case.input


def _retrieval_metrics(
    cases: Sequence[EvalCase], case_results: Mapping[str, Any]
) -> dict[str, Any]:
    metric_cases: list[RetrievalCase] = []
    for case in cases:
        output = _case_output(case, case_results)
        ranked = _first(output, "ranked_chunk_ids", "ranked_ids", "retrieved_chunk_ids")
        relevant = _first(
            case.expected, "relevant_chunk_ids", "relevant_ids", "chunk_ids"
        )
        if relevant is None:
            relevant = _first(
                case.labels, "relevant_chunk_ids", "relevant_ids", "chunk_ids"
            )
        ranked_ids = _string_sequence(
            ranked,
            case_key=case.case_key,
            field="ranked_chunk_ids",
            allow_empty=True,
        )
        relevant_ids = _string_sequence(
            relevant, case_key=case.case_key, field="relevant_chunk_ids"
        )
        grades_value = _first(case.expected, "relevance_grades", "graded_relevance")
        if grades_value is None:
            grades_value = _first(case.labels, "relevance_grades", "graded_relevance")
        grades: Mapping[str, float] | None = None
        if grades_value is not None:
            raw_grades = _mapping(
                grades_value,
                case_key=case.case_key,
                field="relevance_grades",
            )
            grades = {}
            for chunk_id, grade in raw_grades.items():
                if (
                    not isinstance(chunk_id, str)
                    or isinstance(grade, bool)
                    or not isinstance(grade, (int, float))
                ):
                    raise _invalid_result(
                        "relevance_grades must map chunk ids to numbers",
                        case_key=case.case_key,
                        field="relevance_grades",
                    )
                grades[chunk_id] = float(grade)
        try:
            metric_cases.append(
                RetrievalCase(
                    ranked_chunk_ids=ranked_ids,
                    relevant_chunk_ids=frozenset(relevant_ids),
                    relevance_grades=grades,
                )
            )
        except (TypeError, ValueError) as exc:
            raise _invalid_result(str(exc), case_key=case.case_key) from exc
    return evaluate_retrieval(metric_cases).to_dict()


def _routing_metrics(
    cases: Sequence[EvalCase], case_results: Mapping[str, Any]
) -> dict[str, Any]:
    metric_cases: list[RoutingCase] = []
    all_labels: set[str] = set()
    for case in cases:
        output = _case_output(case, case_results)
        predicted = _first(output, "predicted_intent", "intent")
        if not isinstance(predicted, str) or not predicted.strip():
            raise _invalid_result(
                "predicted_intent must be a non-blank string",
                case_key=case.case_key,
                field="predicted_intent",
            )
        predicted = predicted.strip()
        allowed = _first(case.expected, "allowed_intents", "intents")
        expected = _first(case.expected, "expected_intent", "intent")
        if allowed is not None:
            allowed_intents = _string_sequence(
                allowed, case_key=case.case_key, field="allowed_intents"
            )
            expected = predicted if predicted in allowed_intents else allowed_intents[0]
            all_labels.update(allowed_intents)
        if not isinstance(expected, str) or not expected.strip():
            raise _invalid_result(
                "expected_intent must be a non-blank string",
                case_key=case.case_key,
                field="expected_intent",
            )
        expected = expected.strip()
        all_labels.update((expected, predicted))
        metric_cases.append(RoutingCase(expected, predicted))
    return evaluate_routing(metric_cases, labels=tuple(sorted(all_labels))).to_dict()


def _path_metrics(
    cases: Sequence[EvalCase], case_results: Mapping[str, Any]
) -> dict[str, Any]:
    metric_cases: list[PathCase] = []
    exact_matches: list[bool] = []
    path_lengths: list[int] = []
    for case in cases:
        output = _case_output(case, case_results)
        predicted = _first(output, "predicted_concept_ids", "concept_ids", "path")
        predicted_ids = _string_sequence(
            predicted, case_key=case.case_key, field="predicted_concept_ids"
        )
        edge_values = _first(
            case.expected,
            "approved_prerequisite_edges",
            "prerequisite_edges",
        )
        if edge_values is None:
            edge_values = _first(
                case.labels,
                "approved_prerequisite_edges",
                "prerequisite_edges",
            )
        if not isinstance(edge_values, Sequence) or isinstance(
            edge_values, (str, bytes, bytearray)
        ):
            raise _invalid_result(
                "approved_prerequisite_edges must be an array",
                case_key=case.case_key,
                field="approved_prerequisite_edges",
            )
        edges: set[tuple[str, str]] = set()
        for edge in edge_values:
            pair = _string_sequence(
                edge,
                case_key=case.case_key,
                field="approved_prerequisite_edges",
            )
            if len(pair) != 2:
                raise _invalid_result(
                    "Each prerequisite edge must contain exactly two concepts",
                    case_key=case.case_key,
                    field="approved_prerequisite_edges",
                )
            edges.add((pair[0], pair[1]))
        satisfied_value = _first(
            case.input, "satisfied_prerequisite_ids", "mastered_concept_ids"
        )
        satisfied = (
            frozenset()
            if satisfied_value is None
            else frozenset(
                _string_sequence(
                    satisfied_value,
                    case_key=case.case_key,
                    field="satisfied_prerequisite_ids",
                )
            )
        )
        try:
            metric_cases.append(PathCase(predicted_ids, frozenset(edges), satisfied))
        except (TypeError, ValueError) as exc:
            raise _invalid_result(str(exc), case_key=case.case_key) from exc
        teacher_path = _first(
            case.expected,
            "teacher_concept_ids",
            "teacher_path",
            "expected_concept_ids",
        )
        if teacher_path is not None:
            exact_matches.append(
                predicted_ids
                == _string_sequence(
                    teacher_path,
                    case_key=case.case_key,
                    field="teacher_path",
                )
            )
        path_lengths.append(len(predicted_ids))
    result = evaluate_paths(metric_cases).to_dict()
    result["average_path_length"] = sum(path_lengths) / len(path_lengths)
    if exact_matches:
        result["teacher_path_consistency_rate"] = sum(exact_matches) / len(
            exact_matches
        )
        result["teacher_labeled_case_count"] = len(exact_matches)
    return result


def _qa_metrics(
    cases: Sequence[EvalCase], case_results: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    citation_cases: list[CitationCase] = []
    abstention_cases: list[AbstentionCase] = []
    for case in cases:
        output = _case_output(case, case_results)
        required = _first(case.expected, "required_claim_ids", "claim_ids")
        citation_values = _first(output, "citations", "citation_judgments")
        if required is not None or citation_values is not None:
            required_ids = (
                ()
                if required is None
                else _string_sequence(
                    required,
                    case_key=case.case_key,
                    field="required_claim_ids",
                )
            )
            if citation_values is None:
                citation_values = []
            if not isinstance(citation_values, Sequence) or isinstance(
                citation_values, (str, bytes, bytearray)
            ):
                raise _invalid_result(
                    "citations must be an array",
                    case_key=case.case_key,
                    field="citations",
                )
            judgments: list[CitationJudgment] = []
            for position, raw in enumerate(citation_values):
                item = _mapping(
                    raw,
                    case_key=case.case_key,
                    field=f"citations[{position}]",
                )
                claim_id = _first(item, "claim_id", "claim")
                citation_id = _first(item, "citation_id", "citation")
                if not isinstance(claim_id, str) or not isinstance(citation_id, str):
                    raise _invalid_result(
                        "Each citation judgment needs claim_id and citation_id",
                        case_key=case.case_key,
                        field=f"citations[{position}]",
                    )
                judgments.append(
                    CitationJudgment(
                        claim_id=claim_id,
                        citation_id=citation_id,
                        citation_exists=_bool(
                            _first(item, "citation_exists", "exists"),
                            case_key=case.case_key,
                            field="citation_exists",
                        ),
                        belongs_to_expected_scope=_bool(
                            _first(
                                item,
                                "belongs_to_expected_scope",
                                "in_scope",
                            ),
                            case_key=case.case_key,
                            field="belongs_to_expected_scope",
                        ),
                        supports_claim=_bool(
                            _first(item, "supports_claim", "supports"),
                            case_key=case.case_key,
                            field="supports_claim",
                        ),
                    )
                )
            citation_cases.append(
                CitationCase(frozenset(required_ids), tuple(judgments))
            )
        answerable = _first(case.expected, "is_answerable", "answerable")
        refused = _first(output, "refused", "abstained")
        if answerable is not None or refused is not None:
            abstention_cases.append(
                AbstentionCase(
                    is_answerable=_bool(
                        answerable,
                        case_key=case.case_key,
                        field="is_answerable",
                    ),
                    refused=_bool(refused, case_key=case.case_key, field="refused"),
                )
            )
    measured: dict[str, dict[str, Any]] = {}
    if citation_cases:
        measured["citations"] = evaluate_citations(citation_cases).to_dict()
    if abstention_cases:
        measured["abstention"] = evaluate_abstention(abstention_cases).to_dict()
    if not measured:
        raise _invalid_result(
            "End-to-end QA cases contain no deterministic citation or abstention judgments"
        )
    return measured


def evaluate_cases(
    dataset_type: EvalDatasetType,
    cases: Sequence[EvalCase],
    case_results: Mapping[str, Any],
) -> dict[str, Any]:
    if not cases:
        raise _invalid_result("An evaluation run needs at least one active case")
    unknown = sorted(set(case_results).difference(case.case_key for case in cases))
    if unknown:
        raise _invalid_result(
            "case_results contains unknown case keys",
            field=",".join(unknown),
        )
    measured: dict[str, Any]
    if dataset_type == EvalDatasetType.RETRIEVAL:
        measured = {"retrieval": _retrieval_metrics(cases, case_results)}
    elif dataset_type == EvalDatasetType.INTENT_ROUTING:
        measured = {"routing": _routing_metrics(cases, case_results)}
    elif dataset_type == EvalDatasetType.LEARNING_PATH:
        measured = {"paths": _path_metrics(cases, case_results)}
    elif dataset_type == EvalDatasetType.END_TO_END_QA:
        measured = _qa_metrics(cases, case_results)
    else:  # pragma: no cover - protected by the enum
        raise _invalid_result(f"Unsupported evaluation dataset type: {dataset_type}")
    return {
        "schema_version": METRICS_SCHEMA_VERSION,
        "dataset_type": dataset_type.value,
        **measured,
    }


async def create_run(
    session: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    teacher: User,
    raw_config: Mapping[str, Any],
    case_results: Mapping[str, Any],
) -> EvalRun:
    dataset = await _owned_dataset(session, dataset_id, teacher)
    if dataset.status != EvalDatasetStatus.FROZEN or dataset.frozen_at is None:
        raise AppError(
            409,
            "EVAL_DATASET_NOT_FROZEN",
            "Evaluation runs require a frozen dataset",
        )
    index_version = int(raw_config["index_version"])
    course_index = await session.scalar(
        select(CourseIndex).where(
            CourseIndex.course_id == dataset.course_id,
            CourseIndex.version == index_version,
            CourseIndex.deleted_at.is_(None),
        )
    )
    if course_index is None:
        raise AppError(
            404,
            "EVAL_INDEX_NOT_FOUND",
            "The configured course index version was not found",
        )
    if course_index.status not in {
        CourseIndexStatus.READY,
        CourseIndexStatus.ACTIVE,
    }:
        raise AppError(
            409,
            "EVAL_INDEX_NOT_READY",
            "The configured course index version is not ready for evaluation",
        )
    cases = await _active_cases(session, dataset.id)
    snapshot = _domain_snapshot(dataset, cases, frozen_at=dataset.frozen_at)
    try:
        validated_config = EvalConfig(
            git_commit=str(raw_config["git_commit"]),
            dataset_version=str(dataset.version),
            index_version=str(raw_config["index_version"]),
            model_versions=raw_config["model_versions"],
            prompt_version=str(raw_config["prompt_version"]),
            retrieval_parameters=raw_config["retrieval_parameters"],
            hardware=raw_config["hardware"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AppError(
            422,
            "EVAL_CONFIG_INVALID",
            str(exc),
        ) from exc
    requested_dataset_version = raw_config.get("dataset_version")
    if requested_dataset_version is not None and str(requested_dataset_version) != str(
        dataset.version
    ):
        raise AppError(
            409,
            "EVAL_DATASET_VERSION_MISMATCH",
            "The run config dataset version does not match the frozen dataset",
        )

    metrics = evaluate_cases(dataset.type, cases, case_results)
    normalized = validated_config.to_dict()
    normalized["dataset_version"] = dataset.version
    normalized["index_version"] = index_version
    extra_config = {
        key: value
        for key, value in raw_config.items()
        if key
        not in {
            "git_commit",
            "dataset_version",
            "index_version",
            "model_versions",
            "prompt_version",
            "retrieval_parameters",
            "hardware",
        }
    }
    config = {
        **normalized,
        **extra_config,
        "schema_version": RUN_CONFIG_SCHEMA_VERSION,
        "dataset": {
            "id": str(dataset.id),
            "type": dataset.type.value,
            "version": dataset.version,
            "frozen_at": dataset.frozen_at.isoformat(),
            "content_sha256": snapshot.content_sha256,
            "case_count": len(cases),
        },
        "course_index": {
            "id": str(course_index.id),
            "version": course_index.version,
            "status": course_index.status.value,
        },
        "case_results_sha256": _json_digest(case_results),
    }
    # Validate extras before handing the value to SQLAlchemy's JSON serializer.
    _json_digest(config)
    now = datetime.now(UTC)
    run = EvalRun(
        dataset_id=dataset.id,
        config=config,
        status=EvalRunStatus.SUCCEEDED,
        metrics=metrics,
        trace_id=uuid.uuid4(),
        git_commit=validated_config.git_commit,
        index_version=index_version,
        started_at=now,
        finished_at=now,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def queue_run(
    session: AsyncSession,
    *,
    dataset_id: uuid.UUID,
    teacher: User,
    launch_config: Mapping[str, Any],
) -> EvalRun:
    """Create a server-executed evaluation job without accepting measured output."""

    dataset = await _owned_dataset(session, dataset_id, teacher)
    if dataset.status != EvalDatasetStatus.FROZEN or dataset.frozen_at is None:
        raise AppError(
            409,
            "EVAL_DATASET_NOT_FROZEN",
            "Evaluation runs require a frozen dataset",
        )
    index_version = int(launch_config["index_version"])
    if dataset.type == EvalDatasetType.END_TO_END_QA:
        # QA runs measure the student-facing pipeline, which only serves the
        # ACTIVE published index version.
        acceptable_statuses = {CourseIndexStatus.ACTIVE}
    else:
        acceptable_statuses = {CourseIndexStatus.READY, CourseIndexStatus.ACTIVE}
    course_index = await session.scalar(
        select(CourseIndex).where(
            CourseIndex.course_id == dataset.course_id,
            CourseIndex.version == index_version,
            CourseIndex.status.in_(acceptable_statuses),
            CourseIndex.deleted_at.is_(None),
        )
    )
    if course_index is None:
        if dataset.type == EvalDatasetType.END_TO_END_QA:
            raise AppError(
                409,
                "EVAL_QA_INDEX_NOT_ACTIVE",
                "End-to-end QA runs require the ACTIVE course index version",
            )
        raise AppError(
            409,
            "EVAL_INDEX_NOT_READY",
            "The configured course index version is missing or not ready",
        )
    execution = (
        "SERVER_THREE_BASELINE_RUNNER"
        if dataset.type == EvalDatasetType.RETRIEVAL
        else "SERVER_CASE_RUNNER"
    )
    config = {
        "schema_version": RUN_CONFIG_SCHEMA_VERSION,
        "execution": execution,
        "dataset": {"id": str(dataset.id), "version": dataset.version},
        "course_index": {"id": str(course_index.id), "version": index_version},
        "launch": dict(launch_config),
    }
    _json_digest(config)
    run = EvalRun(
        dataset_id=dataset.id,
        config=config,
        status=EvalRunStatus.QUEUED,
        trace_id=uuid.uuid4(),
        git_commit="server-detected-pending",
        index_version=index_version,
    )
    session.add(run)
    await session.commit()
    await session.refresh(run)
    return run


async def get_run(
    session: AsyncSession, *, run_id: uuid.UUID, teacher: User
) -> EvalRun:
    run = await session.scalar(
        select(EvalRun).where(EvalRun.id == run_id, EvalRun.deleted_at.is_(None))
    )
    if run is None:
        raise AppError(404, "EVAL_RUN_NOT_FOUND", "Evaluation run not found")
    await _owned_dataset(session, run.dataset_id, teacher)
    return run


async def _validate_bad_case_source(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    source_type: BadCaseSourceType,
    source_id: uuid.UUID,
) -> None:
    exists: object | None
    if source_type == BadCaseSourceType.MESSAGE:
        exists = await session.scalar(
            select(Message.id)
            .join(ChatSession, ChatSession.id == Message.session_id)
            .where(Message.id == source_id, ChatSession.course_id == course_id)
        )
    elif source_type == BadCaseSourceType.EVAL_RUN:
        exists = await session.scalar(
            select(EvalRun.id)
            .join(EvalDataset, EvalDataset.id == EvalRun.dataset_id)
            .where(EvalRun.id == source_id, EvalDataset.course_id == course_id)
        )
    elif source_type == BadCaseSourceType.QUIZ_ATTEMPT:
        exists = await session.scalar(
            select(QuizAttempt.id)
            .join(QuizItem, QuizItem.id == QuizAttempt.quiz_item_id)
            .where(QuizAttempt.id == source_id, QuizItem.course_id == course_id)
        )
    else:
        message_trace = await session.scalar(
            select(Message.trace_id)
            .join(ChatSession, ChatSession.id == Message.session_id)
            .where(Message.trace_id == source_id, ChatSession.course_id == course_id)
        )
        run_trace = await session.scalar(
            select(EvalRun.trace_id)
            .join(EvalDataset, EvalDataset.id == EvalRun.dataset_id)
            .where(EvalRun.trace_id == source_id, EvalDataset.course_id == course_id)
        )
        exists = message_trace or run_trace
    if exists is None:
        raise AppError(
            404,
            "BAD_CASE_SOURCE_NOT_FOUND",
            "The Bad Case source was not found in this course",
        )


async def create_bad_case(
    session: AsyncSession,
    *,
    course_id: uuid.UUID,
    teacher: User,
    source_type: BadCaseSourceType,
    source_id: uuid.UUID,
    category: str,
    notes: str,
) -> BadCase:
    await require_owned_course(session, course_id, teacher)
    await _validate_bad_case_source(
        session,
        course_id=course_id,
        source_type=source_type,
        source_id=source_id,
    )
    bad_case = BadCase(
        course_id=course_id,
        source_type=source_type,
        source_id=source_id,
        category=category,
        notes=notes,
        reported_by=teacher.id,
    )
    session.add(bad_case)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            409,
            "BAD_CASE_EXISTS",
            "This source already has a Bad Case in the selected category",
        ) from exc
    await session.refresh(bad_case)
    return bad_case


async def update_bad_case(
    session: AsyncSession,
    *,
    bad_case_id: uuid.UUID,
    teacher: User,
    status: BadCaseStatus | None,
    notes: str | None,
) -> BadCase:
    bad_case = await session.scalar(
        select(BadCase)
        .where(BadCase.id == bad_case_id, BadCase.deleted_at.is_(None))
        .with_for_update()
    )
    if bad_case is None:
        raise AppError(404, "BAD_CASE_NOT_FOUND", "Bad Case not found")
    await require_owned_course(session, bad_case.course_id, teacher)
    if notes is not None:
        bad_case.notes = notes
    if status is not None:
        bad_case.status = status
        bad_case.resolved_at = (
            None if status == BadCaseStatus.OPEN else datetime.now(UTC)
        )
    await session.commit()
    await session.refresh(bad_case)
    return bad_case
