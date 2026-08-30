from __future__ import annotations

import sqlite3

from alembic.config import Config

from alembic import command
from app.worker import ping


def test_initial_migration_upgrade_and_downgrade(monkeypatch, tmp_path):
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
        } <= tables
        revision = connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone()[0]
        assert revision == "0001_week1"

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
