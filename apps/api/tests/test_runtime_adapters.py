from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.dialects import postgresql

from app.agent import (
    GenerationPrompt,
    OpenAICompatibleChatAdapter,
    build_openai_chat_adapter,
    parse_openai_answer_draft,
)
from app.config import Settings
from app.models import CourseIndexStatus, DocumentVersionStatus
from app.retrieval import (
    DenseRetrievalUnavailableError,
    PostgresPgVectorDenseRetriever,
)


class _FakeEmbedding:
    def __init__(self, vector: list[float]) -> None:
        self.vector = vector
        self.queries: list[str] = []

    async def embed_query(self, query: str) -> list[float]:
        self.queries.append(query)
        return self.vector


class _FakeRows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.statement = None
        self.covered_version_ids: list[str] | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, statement):
        self.statement = statement
        return _FakeRows(self.rows)

    async def scalar(self, statement):
        # Legacy corpus shape: no coverage recorded.
        return self.covered_version_ids


class _FakeSessionFactory:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    def __call__(self):
        return self.session


async def test_pgvector_dense_query_is_scoped_to_active_published_corpus():
    chunk_id = uuid.uuid4()
    row = (
        SimpleNamespace(
            id=chunk_id,
            content="页面置换选择要换出的物理页。",
            section_path=["虚拟内存", "页面置换"],
            page=42,
        ),
        SimpleNamespace(version=3),
        SimpleNamespace(logical_name="操作系统课件"),
        0.2,
    )
    session = _FakeSession([row])
    session.covered_version_ids = [str(uuid.uuid4())]
    factory = _FakeSessionFactory(session)
    embedding = _FakeEmbedding([0.0] * 1024)
    retriever = PostgresPgVectorDenseRetriever(
        factory,  # type: ignore[arg-type]
        embedding,
        dialect_name="postgresql",
    )

    candidates = await retriever.search(
        course_id=str(uuid.uuid4()),
        index_version="7",
        query="什么是页面置换？",
        top_k=5,
    )

    assert embedding.queries == ["什么是页面置换？"]
    assert len(candidates) == 1
    assert candidates[0].chunk_id == str(chunk_id)
    assert candidates[0].score == pytest.approx(0.8)
    # Calibrated mapping: (0.8 - 0.2) / 0.8. A near-zero similarity must not
    # map to a passing evidence score.
    assert candidates[0].metadata["normalized_score"] == pytest.approx(0.75)
    assert candidates[0].metadata["document"] == "操作系统课件"

    compiled = session.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "<=>" in sql
    assert "course_indexes" in sql
    assert "chunks.version_id" in sql
    # Historical sessions keep dense service from ARCHIVED indexes too; the
    # status set is bound as one expanding list parameter.
    assert [CourseIndexStatus.ACTIVE, CourseIndexStatus.ARCHIVED] in list(
        compiled.params.values()
    )
    assert DocumentVersionStatus.PUBLISHED in compiled.params.values()


async def test_pgvector_dense_query_fails_closed_without_corpus_manifest():
    session = _FakeSession([])
    retriever = PostgresPgVectorDenseRetriever(
        _FakeSessionFactory(session),  # type: ignore[arg-type]
        _FakeEmbedding([0.0] * 1024),
        dialect_name="postgresql",
    )

    candidates = await retriever.search(
        course_id=str(uuid.uuid4()),
        index_version="1",
        query="页面置换",
    )

    assert candidates == []
    compiled = session.statement.compile(dialect=postgresql.dialect())
    assert "chunks.version_id" in str(compiled)


async def test_sqlite_dense_route_fails_before_loading_embedding_model():
    embedding = _FakeEmbedding([0.0] * 1024)
    retriever = PostgresPgVectorDenseRetriever(
        _FakeSessionFactory(_FakeSession([])),  # type: ignore[arg-type]
        embedding,
        dialect_name="sqlite",
    )

    with pytest.raises(DenseRetrievalUnavailableError):
        await retriever.search(
            course_id=str(uuid.uuid4()),
            index_version="1",
            query="页面置换",
        )
    assert embedding.queries == []


def test_openai_adapter_factory_ignores_placeholders_and_models_are_lazy(app_instance):
    placeholder = Settings(
        _env_file=None,
        llm_base_url="https://api.openai.com/v1",
        llm_api_key="",
        llm_model="replace-with-an-openai-compatible-model",
    )
    assert build_openai_chat_adapter(placeholder) is None
    assert app_instance.state.chat_adapter is None
    assert not app_instance.state.embedding_adapter.is_loaded
    assert not app_instance.state.reranker.is_loaded
    assert app_instance.state.dense_retriever.dialect_name == "sqlite"

    configured = placeholder.model_copy(
        update={"llm_api_key": "test-key", "llm_model": "course-chat"}
    )
    adapter = build_openai_chat_adapter(configured)
    assert isinstance(adapter, OpenAICompatibleChatAdapter)
    assert adapter.model == "course-chat"


async def test_openai_adapter_posts_structured_prompt_and_parses_fenced_json():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        body = json.loads(request.content)
        assert body["model"] == "course-chat"
        assert body["response_format"] == {"type": "json_object"}
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": (
                                "```json\n"
                                '{"claims":[{"text":"页框数量有限",'
                                '"citation_labels":[1]}]}\n'
                                "```"
                            )
                        }
                    }
                ]
            },
        )

    adapter = OpenAICompatibleChatAdapter(
        base_url="https://llm.example/v1/",
        api_key="test-key",
        model="course-chat",
        transport=httpx.MockTransport(handler),
    )
    prompt = GenerationPrompt(
        system_instruction="Use only evidence.",
        user_payload='{"COURSE_EVIDENCE":[]}',
        allowed_citation_labels=(1,),
    )

    draft = await adapter.generate(prompt)
    assert draft.claims[0].text == "页框数量有限"
    assert draft.claims[0].citation_labels == [1]

    with pytest.raises(ValueError, match="outside the prompt"):
        parse_openai_answer_draft(
            {
                "choices": [
                    {
                        "message": {
                            "content": {
                                "claims": [
                                    {"text": "unsupported", "citation_labels": [2]}
                                ]
                            }
                        }
                    }
                ]
            },
            allowed_citation_labels=(1,),
        )
