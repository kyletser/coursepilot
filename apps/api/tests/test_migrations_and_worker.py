from __future__ import annotations

import sqlite3

from alembic.config import Config

from alembic import command
from app.worker import ping


def test_migrations_upgrade_and_downgrade(monkeypatch, tmp_path):
    database_path = tmp_path / "migration.db"
    monkeypatch.setenv(
        "DATABASE_URL", f"sqlite+aiosqlite:///{database_path.as_posix()}"
    )
    config = Config("alembic.ini")
    command.upgrade(config, "head")

    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert {
            "alembic_version",
            "users",
            "refresh_tokens",
            "courses",
            "course_invites",
            "enrollments",
            "documents",
            "document_versions",
            "chunks",
            "ingestion_jobs",
            "course_indexes",
            "concept_candidates",
            "relation_candidates",
            "graph_outbox",
            "chat_sessions",
            "messages",
            "citations",
            "quiz_items",
            "quiz_attempts",
            "mastery_states",
            "eval_datasets",
            "eval_cases",
            "eval_runs",
            "bad_cases",
        } <= tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        assert revision == "0004_citation_claim_indices"

    # SQLite reflects named enum CHECK constraints differently from PostgreSQL.
    # CI runs `alembic check` against the real PostgreSQL schema.
    command.downgrade(config, "base")
    with sqlite3.connect(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "users" not in tables


def test_celery_ping_task():
    assert ping.run() == {"status": "ok"}


def test_shared_embedding_adapter_is_a_process_level_singleton(monkeypatch):
    import app.tasks.ingestion as ingestion_module
    from app.config import Settings
    from app.retrieval import BGEM3EmbeddingAdapter

    monkeypatch.setattr(ingestion_module, "_embedding_adapter", None)
    settings = Settings(
        _env_file=None,
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="coursepilot-test-secret-at-least-32-bytes",
    )
    adapter = ingestion_module.shared_embedding_adapter(settings)
    assert isinstance(adapter, BGEM3EmbeddingAdapter)
    # Lazily loaded: constructing or reusing the singleton never loads weights.
    assert adapter.is_loaded is False
    assert ingestion_module.shared_embedding_adapter(settings) is adapter
    monkeypatch.setattr(ingestion_module, "_embedding_adapter", None)
