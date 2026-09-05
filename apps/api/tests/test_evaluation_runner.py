from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.agent.schemas import AnswerDraft, ClaimDraft
from app.evaluation.runner import (
    CASE_EVALUATION_REPORT_SCHEMA_VERSION,
    THREE_BASELINE_REPORT_SCHEMA_VERSION,
    EvaluationProviderFactory,
    EvaluationProviders,
    EvaluationRunnerError,
    run_case_evaluation,
    run_end_to_end_qa_evaluation,
    run_intent_routing_evaluation,
    run_learning_path_evaluation,
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
        case_input: dict[str, object] = {"query": "find target"}
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


# ---------------------------------------------------------------------------
# Case runners: intent routing, learning paths, end-to-end QA
# ---------------------------------------------------------------------------

NOISE_QA_CONTENT = "Unrelated passage about sorting algorithms."
TARGET_QA_CONTENT = "Binary search trees support logarithmic lookups."


class FakeQADense:
    def __init__(self, noise_id, target_id) -> None:
        self.noise_id = noise_id
        self.target_id = target_id

    async def search(self, **_kwargs):
        return [
            DenseCandidate(str(self.noise_id), 0.9, content=NOISE_QA_CONTENT),
            DenseCandidate(str(self.target_id), 0.8, content=TARGET_QA_CONTENT),
        ]


class FakeQALexical:
    def __init__(self, noise_id, target_id) -> None:
        self.noise_id = noise_id
        self.target_id = target_id

    async def search(self, **_kwargs):
        return [
            LexicalCandidate(str(self.noise_id), 3.0, content=NOISE_QA_CONTENT),
            LexicalCandidate(str(self.target_id), 2.0, content=TARGET_QA_CONTENT),
        ]


class FakeQAReranker:
    """Scores the target chunk high unless the query is marked unanswerable."""

    async def score(self, query, documents):
        if "unanswerable" in query.casefold():
            return [0.1 for _ in documents]
        return [
            0.95 if document == TARGET_QA_CONTENT else 0.3 for document in documents
        ]


class FakeChatAdapter:
    model = "fake-chat-v1"

    def __init__(self, claim_text: str) -> None:
        self.claim_text = claim_text
        self.calls = 0

    async def generate(self, _prompt) -> AnswerDraft:
        self.calls += 1
        return AnswerDraft(
            claims=[ClaimDraft(text=self.claim_text, citation_labels=[1])]
        )


class FakeQAProviderFactory(EvaluationProviderFactory):
    def __init__(self, noise_id, target_id) -> None:
        self.noise_id = noise_id
        self.target_id = target_id
        self.calls = 0

    def build(self, **_kwargs) -> EvaluationProviders:
        self.calls += 1
        return EvaluationProviders(
            dense=FakeQADense(self.noise_id, self.target_id),
            lexical=FakeQALexical(self.noise_id, self.target_id),
            reranker=FakeQAReranker(),
            model_versions={
                "embedding": "fake-embedding-v1",
                "reranker": "fake-reranker-v1",
                "graph": "approved-records-v1",
            },
        )


def _concept_candidate(
    *,
    course_id,
    index_id,
    name: str,
    source_chunk_id,
    evidence: str,
    status: ReviewStatus,
    teacher_id,
    reviewed_at,
) -> ConceptCandidate:
    values = {
        "course_id": course_id,
        "index_id": index_id,
        "name": name,
        "description": "",
        "source_chunk_id": source_chunk_id,
        "evidence": evidence,
        "extraction_model": "fixture",
        "confidence": 1.0,
        "status": status,
    }
    if status == ReviewStatus.APPROVED:
        values["reviewed_by"] = teacher_id
        values["reviewed_at"] = reviewed_at
    return ConceptCandidate(**values)


async def _seed_case_base(
    app_instance,
    *,
    index_status: CourseIndexStatus = CourseIndexStatus.ACTIVE,
):
    """Seed course material, an index, and an approved prerequisite graph.

    Mastery states make ``Alpha`` weak (0.1) and ``Beta`` strong (0.9) so the
    planner's weakness-prioritized order is observable.
    """

    now = datetime(2026, 8, 30, 8, 0, tzinfo=UTC)
    async with app_instance.state.session_factory() as session:
        teacher = User(
            email="case-teacher@example.com",
            password_hash="unused",
            role=UserRole.TEACHER,
        )
        student = User(
            email="case-student@example.com",
            password_hash="unused",
            role=UserRole.STUDENT,
        )
        session.add_all([teacher, student])
        await session.flush()
        course = Course(
            owner_id=teacher.id,
            template=CourseTemplate.DATA_STRUCTURES,
            code="DS-CASE",
            name="Case Runner Course",
            description="",
            semester="2026-Fall",
        )
        session.add(course)
        await session.flush()
        session.add(Enrollment(course_id=course.id, student_id=student.id))
        document = Document(course_id=course.id, logical_name="case-material.md")
        session.add(document)
        await session.flush()
        version = DocumentVersion(
            document_id=document.id,
            sha256="b" * 64,
            version=1,
            original_filename="case-material.md",
            media_type="text/markdown",
            file_path="/tmp/case-material.md",
            size_bytes=10,
            status=DocumentVersionStatus.PUBLISHED,
            published_at=now,
        )
        session.add(version)
        await session.flush()
        noise = Chunk(
            version_id=version.id,
            ordinal=0,
            content=NOISE_QA_CONTENT,
            char_count=len(NOISE_QA_CONTENT),
        )
        target = Chunk(
            version_id=version.id,
            ordinal=1,
            content=TARGET_QA_CONTENT,
            char_count=len(TARGET_QA_CONTENT),
        )
        session.add_all([noise, target])
        await session.flush()
        course_index = CourseIndex(
            course_id=course.id,
            version=1,
            dense_status=IndexComponentStatus.READY,
            lexical_status=IndexComponentStatus.READY,
            lexical_path="provided-by-fake-factory",
            status=index_status,
            covered_document_version_ids=[str(version.id)],
        )
        session.add(course_index)
        await session.flush()
        alpha = _concept_candidate(
            course_id=course.id,
            index_id=course_index.id,
            name="Alpha",
            source_chunk_id=noise.id,
            evidence="alpha",
            status=ReviewStatus.APPROVED,
            teacher_id=teacher.id,
            reviewed_at=now,
        )
        beta = _concept_candidate(
            course_id=course.id,
            index_id=course_index.id,
            name="Beta",
            source_chunk_id=noise.id,
            evidence="beta",
            status=ReviewStatus.APPROVED,
            teacher_id=teacher.id,
            reviewed_at=now,
        )
        trees = _concept_candidate(
            course_id=course.id,
            index_id=course_index.id,
            name="Trees",
            source_chunk_id=target.id,
            evidence="trees",
            status=ReviewStatus.APPROVED,
            teacher_id=teacher.id,
            reviewed_at=now,
        )
        pending = _concept_candidate(
            course_id=course.id,
            index_id=course_index.id,
            name="Pending",
            source_chunk_id=target.id,
            evidence="pending",
            status=ReviewStatus.PENDING,
            teacher_id=teacher.id,
            reviewed_at=now,
        )
        session.add_all([alpha, beta, trees, pending])
        await session.flush()
        session.add_all(
            [
                RelationCandidate(
                    course_id=course.id,
                    from_candidate_id=alpha.id,
                    to_candidate_id=trees.id,
                    type=RelationType.PREREQUISITE_OF,
                    source_chunk_id=target.id,
                    evidence="approved alpha prerequisite",
                    extraction_model="fixture",
                    confidence=1.0,
                    status=ReviewStatus.APPROVED,
                    reviewed_by=teacher.id,
                    reviewed_at=now,
                ),
                RelationCandidate(
                    course_id=course.id,
                    from_candidate_id=beta.id,
                    to_candidate_id=trees.id,
                    type=RelationType.PREREQUISITE_OF,
                    source_chunk_id=target.id,
                    evidence="approved beta prerequisite",
                    extraction_model="fixture",
                    confidence=1.0,
                    status=ReviewStatus.APPROVED,
                    reviewed_by=teacher.id,
                    reviewed_at=now,
                ),
            ]
        )
        session.add_all(
            [
                MasteryState(
                    course_id=course.id,
                    student_id=student.id,
                    concept_id=alpha.id,
                    alpha=1.0,
                    beta=9.0,
                ),
                MasteryState(
                    course_id=course.id,
                    student_id=student.id,
                    concept_id=beta.id,
                    alpha=9.0,
                    beta=1.0,
                ),
            ]
        )
        await session.commit()
        return {
            "teacher_id": teacher.id,
            "student_id": student.id,
            "course_id": course.id,
            "noise_id": noise.id,
            "target_id": target.id,
            "alpha_id": alpha.id,
            "beta_id": beta.id,
            "trees_id": trees.id,
            "pending_id": pending.id,
        }


async def _add_frozen_dataset(
    app_instance,
    *,
    course_id,
    teacher_id,
    dataset_type: EvalDatasetType,
    cases,
    name: str,
    version: int = 1,
):
    frozen_at = datetime(2026, 8, 30, 8, 30, tzinfo=UTC)
    async with app_instance.state.session_factory() as session:
        dataset = EvalDataset(
            course_id=course_id,
            name=name,
            description="",
            type=dataset_type,
            version=version,
            status=EvalDatasetStatus.FROZEN,
            created_by=teacher_id,
            frozen_at=frozen_at,
        )
        session.add(dataset)
        await session.flush()
        for case in cases:
            session.add(
                EvalCase(
                    dataset_id=dataset.id,
                    case_key=case["case_key"],
                    input=case["input"],
                    expected=case.get("expected", {}),
                    labels=case.get("labels", {}),
                )
            )
        await session.commit()
        return dataset.id


_PROVENANCE_KWARGS = {
    "git_commit": "0123456789abcdef",
    "git_dirty": False,
    "generated_at": datetime(2026, 8, 30, 9, 0, tzinfo=UTC),
    "hardware": {"cpu": "test-cpu"},
}


async def test_case_runner_measures_deterministic_intent_routing(
    app_instance, tmp_path
):
    base = await _seed_case_base(app_instance)
    dataset_id = await _add_frozen_dataset(
        app_instance,
        course_id=base["course_id"],
        teacher_id=base["teacher_id"],
        dataset_type=EvalDatasetType.INTENT_ROUTING,
        name="Frozen routing fixture",
        cases=[
            {
                "case_key": "learning-path",
                "input": {"query": "我应该按什么学习路径推进？"},
                "expected": {"expected_intent": "LEARNING_PATH"},
            },
            {
                "case_key": "quiz",
                "input": {"query": "请给我出几道测验题"},
                "expected": {"expected_intent": "QUIZ"},
            },
            {
                "case_key": "diagnose",
                "input": {"query": "帮我诊断知识缺口"},
                "expected": {"expected_intent": "DIAGNOSE"},
            },
            {
                "case_key": "compare",
                "input": {"query": "比较栈和队列的区别"},
                "expected": {"allowed_intents": ["CONCEPT_COMPARE", "TUTOR_QA"]},
            },
            {
                "case_key": "tutor-qa",
                "input": {"query": "什么是二叉搜索树？"},
                "expected": {"expected_intent": "TUTOR_QA"},
            },
        ],
    )

    result = await run_case_evaluation(
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        dataset_id=dataset_id,
        dataset_type=EvalDatasetType.INTENT_ROUTING,
        index_version=1,
        output_dir=tmp_path,
        **_PROVENANCE_KWARGS,
    )

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CASE_EVALUATION_REPORT_SCHEMA_VERSION
    assert (
        payload["provenance"]["prompt_version"]
        == "no-generation/intent-routing-eval-v1"
    )
    case_results = payload["case_results"]
    assert case_results["learning-path"]["predicted_intent"] == "LEARNING_PATH"
    assert case_results["learning-path"]["needs_retrieval"] is False
    assert case_results["quiz"]["predicted_intent"] == "QUIZ"
    assert case_results["diagnose"]["predicted_intent"] == "DIAGNOSE"
    assert case_results["compare"]["predicted_intent"] == "CONCEPT_COMPARE"
    assert case_results["tutor-qa"]["predicted_intent"] == "TUTOR_QA"
    assert case_results["tutor-qa"]["needs_retrieval"] is True
    assert payload["metrics"]["routing"]["macro_f1"] == 1.0
    assert payload["metrics"]["routing"]["case_count"] == 5

    async with app_instance.state.session_factory() as session:
        runs = (await session.scalars(select(EvalRun))).all()
        assert len(runs) == 1
        assert runs[0].config["experiment"] == "INTENT_ROUTING"
        assert runs[0].metrics["routing"]["macro_f1"] == 1.0


async def test_case_runner_plans_learning_paths_from_approved_graph(
    app_instance, tmp_path
):
    base = await _seed_case_base(app_instance)
    dataset_id = await _add_frozen_dataset(
        app_instance,
        course_id=base["course_id"],
        teacher_id=base["teacher_id"],
        dataset_type=EvalDatasetType.LEARNING_PATH,
        name="Frozen learning path fixture",
        cases=[
            {
                "case_key": "weak-first",
                "input": {
                    "student_id": str(base["student_id"]),
                    "target_concept_id": str(base["trees_id"]),
                },
                "expected": {
                    "approved_prerequisite_edges": [
                        [str(base["alpha_id"]), str(base["trees_id"])],
                        [str(base["beta_id"]), str(base["trees_id"])],
                    ],
                    "teacher_concept_ids": [
                        str(base["alpha_id"]),
                        str(base["beta_id"]),
                        str(base["trees_id"]),
                    ],
                },
            },
        ],
    )

    result = await run_case_evaluation(
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        dataset_id=dataset_id,
        dataset_type=EvalDatasetType.LEARNING_PATH,
        index_version=1,
        output_dir=tmp_path,
        **_PROVENANCE_KWARGS,
    )

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CASE_EVALUATION_REPORT_SCHEMA_VERSION
    planned = payload["case_results"]["weak-first"]
    # The weak prerequisite (mastery 0.1) must be ordered before the strong one.
    assert planned["predicted_concept_ids"] == [
        str(base["alpha_id"]),
        str(base["beta_id"]),
        str(base["trees_id"]),
    ]
    steps = {step["concept_id"]: step for step in planned["steps"]}
    assert steps[str(base["alpha_id"])]["reason"] == "WEAK_PREREQUISITE"
    assert steps[str(base["alpha_id"])]["is_weak"] is True
    assert steps[str(base["beta_id"])]["reason"] == "PREREQUISITE"
    assert steps[str(base["beta_id"])]["is_weak"] is False
    assert steps[str(base["trees_id"])]["reason"] == "TARGET"
    assert steps[str(base["trees_id"])]["depth"] == 0

    path_metrics = payload["metrics"]["paths"]
    assert path_metrics["prerequisite_legality_rate"] == 1.0
    assert path_metrics["fully_legal_path_rate"] == 1.0
    assert path_metrics["teacher_path_consistency_rate"] == 1.0
    assert path_metrics["average_path_length"] == 3.0

    async with app_instance.state.session_factory() as session:
        runs = (await session.scalars(select(EvalRun))).all()
        assert len(runs) == 1
        assert runs[0].config["experiment"] == "LEARNING_PATH"


async def test_learning_path_runner_rejects_unapproved_target(app_instance, tmp_path):
    base = await _seed_case_base(app_instance)
    dataset_id = await _add_frozen_dataset(
        app_instance,
        course_id=base["course_id"],
        teacher_id=base["teacher_id"],
        dataset_type=EvalDatasetType.LEARNING_PATH,
        name="Frozen unapproved target fixture",
        cases=[
            {
                "case_key": "pending-target",
                "input": {
                    "student_id": str(base["student_id"]),
                    "target_concept_id": str(base["pending_id"]),
                },
                "expected": {"approved_prerequisite_edges": []},
            },
        ],
    )

    with pytest.raises(EvaluationRunnerError) as captured:
        await run_learning_path_evaluation(
            session_factory=app_instance.state.session_factory,
            settings=app_instance.state.settings,
            dataset_id=dataset_id,
            index_version=1,
            output_dir=tmp_path,
            git_commit="0123456789abcdef",
            hardware={"cpu": "test-cpu"},
        )

    assert captured.value.code == "EVAL_PATH_TARGET_NOT_APPROVED"
    assert captured.value.details == {"case_key": "pending-target"}
    async with app_instance.state.session_factory() as session:
        assert await session.scalar(select(func.count(EvalRun.id))) == 0


async def test_case_runner_executes_end_to_end_qa_pipeline(app_instance, tmp_path):
    base = await _seed_case_base(app_instance)
    dataset_id = await _add_frozen_dataset(
        app_instance,
        course_id=base["course_id"],
        teacher_id=base["teacher_id"],
        dataset_type=EvalDatasetType.END_TO_END_QA,
        name="Frozen QA fixture",
        cases=[
            {
                "case_key": "answerable",
                "input": {
                    "query": "What do binary search trees support?",
                    "student_id": str(base["student_id"]),
                },
                "expected": {
                    "is_answerable": True,
                    "relevant_chunk_ids": [str(base["target_id"])],
                    "required_claim_ids": ["claim-lookup"],
                },
                "labels": {
                    "claims": {
                        "claim-lookup": {
                            "text": TARGET_QA_CONTENT,
                            "supporting_chunk_ids": [str(base["target_id"])],
                        }
                    }
                },
            },
            {
                "case_key": "unanswerable",
                "input": {
                    "query": "An unanswerable question about quantum gravity",
                    "student_id": str(base["student_id"]),
                },
                "expected": {
                    "is_answerable": False,
                    "relevant_chunk_ids": [str(base["target_id"])],
                },
                "labels": {
                    "claims": {
                        "claim-none": {
                            "text": "No supporting claim",
                            "supporting_chunk_ids": [str(base["target_id"])],
                        }
                    }
                },
            },
        ],
    )
    factory = FakeQAProviderFactory(base["noise_id"], base["target_id"])
    chat_adapter = FakeChatAdapter(TARGET_QA_CONTENT)

    result = await run_case_evaluation(
        session_factory=app_instance.state.session_factory,
        settings=app_instance.state.settings,
        dataset_id=dataset_id,
        dataset_type=EvalDatasetType.END_TO_END_QA,
        index_version=1,
        output_dir=tmp_path,
        provider_factory=factory,
        chat_adapter_factory=lambda: chat_adapter,
        **_PROVENANCE_KWARGS,
    )

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == CASE_EVALUATION_REPORT_SCHEMA_VERSION
    assert factory.calls == 1

    answerable = payload["case_results"]["answerable"]
    assert answerable["refused"] is False
    assert answerable["agent_status"] == "ANSWERED"
    assert answerable["citations"] == [
        {
            "claim_id": "claim-lookup",
            "citation_id": str(base["target_id"]),
            "citation_exists": True,
            "belongs_to_expected_scope": True,
            "supports_claim": True,
        }
    ]
    assert chat_adapter.calls == 1

    unanswerable = payload["case_results"]["unanswerable"]
    assert unanswerable["refused"] is True
    assert unanswerable["agent_status"] == "INSUFFICIENT_EVIDENCE"
    assert unanswerable["citations"] == []
    # The low rerank scores must refuse without ever invoking the chat model.
    assert chat_adapter.calls == 1

    assert payload["metrics"]["citations"]["citation_accuracy"] == 1.0
    assert payload["metrics"]["citations"]["citation_coverage"] == 1.0
    assert payload["metrics"]["abstention"]["unanswerable_refusal_rate"] == 1.0
    assert payload["metrics"]["abstention"]["false_refusal_rate"] == 0.0
    assert payload["provenance"]["model_versions"]["chat"] == "fake-chat-v1"

    async with app_instance.state.session_factory() as session:
        runs = (await session.scalars(select(EvalRun))).all()
        assert len(runs) == 1
        assert runs[0].config["experiment"] == "END_TO_END_QA"
        assert runs[0].metrics["abstention"]["unanswerable_refusal_rate"] == 1.0


async def test_qa_runner_requires_active_index(app_instance, tmp_path):
    base = await _seed_case_base(app_instance, index_status=CourseIndexStatus.READY)
    dataset_id = await _add_frozen_dataset(
        app_instance,
        course_id=base["course_id"],
        teacher_id=base["teacher_id"],
        dataset_type=EvalDatasetType.END_TO_END_QA,
        name="Frozen QA ready-index fixture",
        cases=[
            {
                "case_key": "answerable",
                "input": {"query": "What do binary search trees support?"},
                "expected": {
                    "is_answerable": True,
                    "relevant_chunk_ids": [str(base["target_id"])],
                },
                "labels": {
                    "claims": {
                        "claim-lookup": {
                            "text": TARGET_QA_CONTENT,
                            "supporting_chunk_ids": [str(base["target_id"])],
                        }
                    }
                },
            },
        ],
    )

    with pytest.raises(EvaluationRunnerError) as captured:
        await run_end_to_end_qa_evaluation(
            session_factory=app_instance.state.session_factory,
            settings=app_instance.state.settings,
            dataset_id=dataset_id,
            index_version=1,
            output_dir=tmp_path,
            provider_factory=FakeQAProviderFactory(base["noise_id"], base["target_id"]),
            chat_adapter_factory=lambda: FakeChatAdapter(TARGET_QA_CONTENT),
            git_commit="0123456789abcdef",
            hardware={"cpu": "test-cpu"},
        )

    assert captured.value.code == "EVAL_QA_INDEX_NOT_ACTIVE"
    async with app_instance.state.session_factory() as session:
        assert await session.scalar(select(func.count(EvalRun.id))) == 0


async def test_qa_runner_requires_labels_before_running(app_instance, tmp_path):
    base = await _seed_case_base(app_instance)
    missing_claims_id = await _add_frozen_dataset(
        app_instance,
        course_id=base["course_id"],
        teacher_id=base["teacher_id"],
        dataset_type=EvalDatasetType.END_TO_END_QA,
        name="Frozen QA missing claims fixture",
        cases=[
            {
                "case_key": "missing-labels",
                "input": {"query": "What do binary search trees support?"},
                "expected": {"is_answerable": True},
            },
        ],
    )
    missing_answerability_id = await _add_frozen_dataset(
        app_instance,
        course_id=base["course_id"],
        teacher_id=base["teacher_id"],
        dataset_type=EvalDatasetType.END_TO_END_QA,
        name="Frozen QA missing answerability fixture",
        version=2,
        cases=[
            {
                "case_key": "missing-answerability",
                "input": {"query": "What do binary search trees support?"},
                "expected": {"relevant_chunk_ids": [str(base["target_id"])]},
                "labels": {
                    "claims": {
                        "claim-lookup": {
                            "text": TARGET_QA_CONTENT,
                            "supporting_chunk_ids": [str(base["target_id"])],
                        }
                    }
                },
            },
        ],
    )

    with pytest.raises(EvaluationRunnerError) as claims_error:
        await run_end_to_end_qa_evaluation(
            session_factory=app_instance.state.session_factory,
            settings=app_instance.state.settings,
            dataset_id=missing_claims_id,
            index_version=1,
            output_dir=tmp_path,
            provider_factory=FakeQAProviderFactory(base["noise_id"], base["target_id"]),
            chat_adapter_factory=lambda: FakeChatAdapter(TARGET_QA_CONTENT),
            git_commit="0123456789abcdef",
            hardware={"cpu": "test-cpu"},
        )
    assert claims_error.value.code == "EVAL_QA_CLAIMS_MISSING"

    with pytest.raises(EvaluationRunnerError) as answerability_error:
        await run_end_to_end_qa_evaluation(
            session_factory=app_instance.state.session_factory,
            settings=app_instance.state.settings,
            dataset_id=missing_answerability_id,
            index_version=1,
            output_dir=tmp_path,
            provider_factory=FakeQAProviderFactory(base["noise_id"], base["target_id"]),
            chat_adapter_factory=lambda: FakeChatAdapter(TARGET_QA_CONTENT),
            git_commit="0123456789abcdef",
            hardware={"cpu": "test-cpu"},
        )
    assert answerability_error.value.code == "EVAL_QA_ANSWERABILITY_MISSING"

    async with app_instance.state.session_factory() as session:
        assert await session.scalar(select(func.count(EvalRun.id))) == 0


async def test_case_runner_rejects_retrieval_datasets(app_instance, tmp_path):
    dataset_id, _, _ = await _seed_runner_data(app_instance)

    with pytest.raises(EvaluationRunnerError) as captured:
        await run_case_evaluation(
            session_factory=app_instance.state.session_factory,
            settings=app_instance.state.settings,
            dataset_id=dataset_id,
            dataset_type=EvalDatasetType.RETRIEVAL,
            index_version=1,
            output_dir=tmp_path,
            git_commit="0123456789abcdef",
            hardware={"cpu": "test-cpu"},
        )

    assert captured.value.code == "EVAL_DATASET_TYPE_INVALID"


async def test_case_runners_reject_dataset_type_mismatch(app_instance, tmp_path):
    dataset_id, _, _ = await _seed_runner_data(app_instance)

    with pytest.raises(EvaluationRunnerError) as captured:
        await run_intent_routing_evaluation(
            session_factory=app_instance.state.session_factory,
            settings=app_instance.state.settings,
            dataset_id=dataset_id,
            index_version=1,
            output_dir=tmp_path,
            git_commit="0123456789abcdef",
            hardware={"cpu": "test-cpu"},
        )

    assert captured.value.code == "EVAL_DATASET_TYPE_INVALID"
