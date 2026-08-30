from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.evaluation.runner import (
    THREE_BASELINE_REPORT_SCHEMA_VERSION,
    EvaluationProviderFactory,
    EvaluationProviders,
    EvaluationRunnerError,
    run_three_baselines,
)
from app.models import (
    Chunk,
    ConceptCandidate,
    Course,
    CourseIndex,
    CourseIndexStatus,
    CourseTemplate,
    Document,
    DocumentVersion,
    DocumentVersionStatus,
    Enrollment,
    EvalCase,
    EvalDataset,
    EvalDatasetStatus,
    EvalDatasetType,
    EvalRun,
    IndexComponentStatus,
    MasteryState,
    RelationCandidate,
    RelationType,
    ReviewStatus,
    User,
    UserRole,
)
from app.retrieval import DenseCandidate, LexicalCandidate


class FakeDense:
    def __init__(self, noise: Chunk, target: Chunk) -> None:
        self.noise = noise
        self.target = target

    async def search(self, **_kwargs):
        return [
            DenseCandidate(str(self.noise.id), 0.9, content="noise"),
            DenseCandidate(str(self.target.id), 0.8, content="target"),
        ]


class FakeLexical:
    def __init__(self, noise: Chunk, target: Chunk) -> None:
        self.noise = noise
        self.target = target

    async def search(self, **_kwargs):
        return [
            LexicalCandidate(str(self.noise.id), 3.0, content="noise"),
            LexicalCandidate(str(self.target.id), 2.0, content="target"),
        ]


class FakeReranker:
    async def score(self, _query, documents):
        return [0.9 if document == "noise" else 0.1 for document in documents]


class FakeProviderFactory(EvaluationProviderFactory):
    def __init__(self, noise: Chunk, target: Chunk) -> None:
        self.noise = noise
        self.target = target
        self.calls = 0

    def build(self, **_kwargs) -> EvaluationProviders:
        self.calls += 1
        return EvaluationProviders(
            dense=FakeDense(self.noise, self.target),
            lexical=FakeLexical(self.noise, self.target),
            reranker=FakeReranker(),
            model_versions={
                "embedding": "fake-embedding-v1",
                "reranker": "fake-reranker-v1",
                "graph": "approved-records-v1",
            },
        )


async def _seed_runner_data(app_instance, *, include_context: bool = True):
    now = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
    async with app_instance.state.session_factory() as session:
        teacher = User(
            email="runner-teacher@example.com",
            password_hash="unused",
            role=UserRole.TEACHER,
        )
        student = User(
            email="runner-student@example.com",
            password_hash="unused",
            role=UserRole.STUDENT,
        )
        session.add_all([teacher, student])
        await session.flush()
        course = Course(
            owner_id=teacher.id,
            template=CourseTemplate.DATA_STRUCTURES,
            code="DS-EVAL",
            name="Data Structures Evaluation",
            description="",
            semester="2026-Fall",
        )
        session.add(course)
        await session.flush()
        session.add(Enrollment(course_id=course.id, student_id=student.id))
        document = Document(course_id=course.id, logical_name="evaluation.md")
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            document_id=document.id,
            sha256="a" * 64,
            version=1,
            original_filename="evaluation.md",
            media_type="text/markdown",
            file_path="/tmp/evaluation.md",
            size_bytes=10,
            status=DocumentVersionStatus.PUBLISHED,
            published_at=now,
        )
        session.add(version)
        await session.flush()
        noise = Chunk(
            version_id=version.id,
            ordinal=0,
            content="noise",
            char_count=5,
        )
        target = Chunk(
            version_id=version.id,
            ordinal=1,
            content="target",
            char_count=6,
        )
        session.add_all([noise, target])
        await session.flush()
        course_index = CourseIndex(
            course_id=course.id,
            version=1,
            dense_status=IndexComponentStatus.READY,
            lexical_status=IndexComponentStatus.READY,
            lexical_path="provided-by-fake-factory",
            status=CourseIndexStatus.ACTIVE,
        )
        session.add(course_index)
        await session.flush()
        target_concept = ConceptCandidate(
            course_id=course.id,
            index_id=course_index.id,
            name="Target",
            description="",
            source_chunk_id=target.id,
            evidence="target",
            extraction_model="fixture",
            confidence=1.0,
            status=ReviewStatus.APPROVED,
            reviewed_by=teacher.id,
            reviewed_at=now,
        )
        prerequisite = ConceptCandidate(
            course_id=course.id,
            index_id=course_index.id,
            name="Prerequisite",
            description="",
            source_chunk_id=noise.id,
            evidence="noise",
            extraction_model="fixture",
            confidence=1.0,
            status=ReviewStatus.APPROVED,
            reviewed_by=teacher.id,
            reviewed_at=now,
        )
        session.add_all([target_concept, prerequisite])
        await session.flush()
        session.add(
            RelationCandidate(
                course_id=course.id,
                from_candidate_id=prerequisite.id,
                to_candidate_id=target_concept.id,
                type=RelationType.PREREQUISITE_OF,
                source_chunk_id=target.id,
                evidence="approved relation",
                extraction_model="fixture",
                confidence=1.0,
                status=ReviewStatus.APPROVED,
                reviewed_by=teacher.id,
                reviewed_at=now,
            )
        )
        session.add(
            MasteryState(
                course_id=course.id,
                student_id=student.id,
                concept_id=target_concept.id,
                alpha=1.0,
                beta=9.0,
            )
        )
        dataset = EvalDataset(
            course_id=course.id,
            name="Frozen retrieval runner fixture",
            description="",
            type=EvalDatasetType.RETRIEVAL,
            version=1,
            status=EvalDatasetStatus.FROZEN,
            created_by=teacher.id,
            frozen_at=now,
        )
        session.add(dataset)
        await session.flush()
        case_input = {"query": "find target"}
        if include_context:
            case_input.update(
                {
                    "student_id": str(student.id),
                    "target_concept_ids": [str(target_concept.id)],
                }
            )
        session.add(
            EvalCase(
                dataset_id=dataset.id,
                case_key="target-case",
                input=case_input,
                expected={"relevant_chunk_ids": [str(target.id)]},
                labels={},
            )
        )
        await session.commit()
        return dataset.id, noise.id, target.id


async def test_runner_executes_and_persists_three_real_rankings(app_instance, tmp_path):
    dataset_id, noise_id, target_id = await _seed_runner_data(app_instance)
    async with app_instance.state.session_factory() as session:
        noise = await session.get(Chunk, noise_id)
        target = await session.get(Chunk, target_id)
        assert noise is not None and target is not None
    factory = FakeProviderFactory(noise, target)

    result = await run_three_baselines(
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        dataset_id=dataset_id,
        index_version=1,
        output_dir=tmp_path,
        provider_factory=factory,
        git_commit="0123456789abcdef",
        git_dirty=True,
        report_version="fixture-three-baselines-v1",
        generated_at=datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
        hardware={"cpu": "test-cpu"},
        final_top_k=2,
    )

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    by_mode = {item["mode"]: item for item in payload["baselines"]}
    assert factory.calls == 1
    assert payload["schema_version"] == THREE_BASELINE_REPORT_SCHEMA_VERSION
    assert payload["provenance"]["git_dirty"] is True
    assert set(by_mode) == {"dense_only", "hybrid_rerank", "kg_personalized"}
    assert by_mode["dense_only"]["case_results"]["target-case"]["ranked_chunk_ids"] == [
        str(noise_id),
        str(target_id),
    ]
    assert by_mode["hybrid_rerank"]["case_results"]["target-case"][
        "ranked_chunk_ids"
    ] == [str(noise_id), str(target_id)]
    personalized = by_mode["kg_personalized"]["case_results"]["target-case"]
    assert personalized["ranked_chunk_ids"][0] == str(target_id)
    assert personalized["personalization_trace"]["mastery_states_used"]
    assert personalized["personalization_trace"]["approved_relation_ids_used"]
    assert by_mode["dense_only"]["metrics"]["retrieval"]["mrr_at_5"] == 0.5
    assert by_mode["kg_personalized"]["metrics"]["retrieval"]["mrr_at_5"] == 1.0

    async with app_instance.state.session_factory() as session:
        run_count = await session.scalar(select(func.count(EvalRun.id)))
        assert run_count == 3
        experiments = set(
            (await session.scalars(select(EvalRun.config["experiment"]))).all()
        )
        assert experiments == {
            "dense_only",
            "hybrid_rerank",
            "kg_personalized",
        }


async def test_runner_rejects_missing_personalization_context_before_any_run(
    app_instance, tmp_path
):
    dataset_id, noise_id, target_id = await _seed_runner_data(
        app_instance, include_context=False
    )
    async with app_instance.state.session_factory() as session:
        noise = await session.get(Chunk, noise_id)
        target = await session.get(Chunk, target_id)
        assert noise is not None and target is not None
    factory = FakeProviderFactory(noise, target)

    with pytest.raises(EvaluationRunnerError) as captured:
        await run_three_baselines(
            session_factory=app_instance.state.session_factory,
            settings=app_instance.state.settings,
            dataset_id=dataset_id,
            index_version=1,
            output_dir=tmp_path,
            provider_factory=factory,
            git_commit="0123456789abcdef",
            hardware={"cpu": "test-cpu"},
        )

    assert captured.value.code == "EVAL_PERSONALIZATION_CONTEXT_MISSING"
    assert captured.value.details == {"case_key": "target-case"}
    assert factory.calls == 0
    async with app_instance.state.session_factory() as session:
        assert await session.scalar(select(func.count(EvalRun.id))) == 0
