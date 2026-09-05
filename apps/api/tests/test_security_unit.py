from __future__ import annotations

import uuid
from datetime import UTC, datetime

import jwt
import pytest

from app.config import Settings
from app.errors import AppError
from app.models import User, UserRole
from app.security import PasswordService, TokenService, hash_secret


@pytest.fixture
def security_settings() -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="coursepilot-test-secret-at-least-32-bytes",
        argon2_time_cost=1,
        argon2_memory_cost=8192,
        argon2_parallelism=1,
        readiness_check_migrations=False,
        readiness_check_redis=False,
        readiness_check_neo4j=False,
        readiness_check_index=False,
    )


def test_argon2id_hash_and_sha256_digest(security_settings):
    service = PasswordService(security_settings)
    first = service.hash("a sufficiently long password")
    second = service.hash("a sufficiently long password")
    assert first.startswith("$argon2id$")
    assert first != second
    assert service.verify(first, "a sufficiently long password")
    assert not service.verify(first, "wrong")
    assert not service.verify("broken", "anything")

    digest = hash_secret("opaque token")
    assert len(digest) == 64
    assert digest == hash_secret("opaque token")
    assert "opaque token" not in digest


def test_access_jwt_enforces_claims_and_algorithm(security_settings):
    service = TokenService(security_settings)
    user = User(
        id=uuid.uuid4(),
        email="s@example.com",
        password_hash="x",
        role=UserRole.STUDENT,
    )
    token = service.create_access_token(user)
    claims = service.decode_access_token(token)
    assert claims["sub"] == str(user.id)
    assert claims["type"] == "access"
    assert claims["iss"] == security_settings.jwt_issuer
    assert claims["aud"] == security_settings.jwt_audience
    assert claims["exp"] - claims["iat"] == 30 * 60

    bad_type = jwt.encode(
        {
            **claims,
            "type": "refresh",
            "iat": datetime.now(UTC),
            "nbf": datetime.now(UTC),
        },
        security_settings.jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(AppError, match="type"):
        service.decode_access_token(bad_type)

    with pytest.raises(AppError):
        service.decode_access_token(token + "tampered")


def test_refresh_tokens_are_high_entropy_opaque_values(security_settings):
    token = TokenService(security_settings).create_refresh_token()
    assert token.startswith("cp_rt_")
    assert len(token) > 64
    assert token.count(".") != 2
