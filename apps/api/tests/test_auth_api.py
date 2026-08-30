from __future__ import annotations

import uuid

from sqlalchemy import select

from app.models import RefreshToken, User
from tests.helpers import auth_headers, login, register, register_and_login


async def test_register_login_and_me_store_only_argon2id_hash(client, app_instance):
    registered = await register(
        client, "Teacher@Example.COM", role="TEACHER", password=" a real password "
    )
    assert registered["email"] == "teacher@example.com"
    assert registered["role"] == "TEACHER"
    assert "password" not in registered

    async with app_instance.state.session_factory() as session:
        user = await session.scalar(
            select(User).where(User.email == "teacher@example.com")
        )
        assert user is not None
        assert user.password_hash.startswith("$argon2id$")
        assert " a real password " not in user.password_hash

    tokens = await login(client, "TEACHER@example.com", password=" a real password ")
    assert tokens["token_type"] == "bearer"
    assert tokens["expires_in"] == 1800
    assert 14 * 86400 - 10 <= tokens["refresh_expires_in"] <= 14 * 86400

    me = await client.get("/api/v1/auth/me", headers=auth_headers(tokens))
    assert me.status_code == 200
    assert me.json()["data"]["id"] == registered["id"]
    assert me.json()["data"]["email"] == "teacher@example.com"


async def test_duplicate_email_and_login_failures_do_not_enumerate(client):
    await register(client, "student@example.com", role="STUDENT")
    duplicate = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "STUDENT@example.com",
            "password": "another password",
            "role": "STUDENT",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "EMAIL_ALREADY_REGISTERED"

    wrong = await client.post(
        "/api/v1/auth/login",
        json={"email": "student@example.com", "password": "wrong"},
    )
    absent = await client.post(
        "/api/v1/auth/login",
        json={"email": "absent@example.com", "password": "wrong"},
    )
    assert wrong.status_code == absent.status_code == 401
    assert wrong.json()["error"] == absent.json()["error"]


async def test_refresh_rotation_reuse_revokes_active_family(client, app_instance):
    user, initial = await register_and_login(
        client, "rotate@example.com", role="STUDENT"
    )
    first_refresh = initial["refresh_token"]

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert rotated.status_code == 200
    second = rotated.json()["data"]
    assert second["refresh_token"] != first_refresh
    assert second["access_token"] != initial["access_token"]

    async with app_instance.state.session_factory() as session:
        records = (
            await session.scalars(
                select(RefreshToken)
                .where(RefreshToken.user_id == uuid.UUID(user["id"]))
                .order_by(RefreshToken.created_at)
            )
        ).all()
        assert len(records) == 2
        parent, child = records
        assert parent.consumed_at is not None
        assert parent.revoked_at is not None
        assert child.parent_id == parent.id
        assert child.family_id == parent.family_id
        assert child.expires_at == parent.expires_at
        assert child.revoked_at is None

    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": first_refresh}
    )
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "REFRESH_TOKEN_REUSE_DETECTED"

    active_child = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second["refresh_token"]}
    )
    assert active_child.status_code == 401
    assert active_child.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"

    async with app_instance.state.session_factory() as session:
        records = (
            await session.scalars(
                select(RefreshToken).where(
                    RefreshToken.user_id == uuid.UUID(user["id"])
                )
            )
        ).all()
        assert all(record.revoked_at is not None for record in records)


async def test_logout_is_idempotent_and_does_not_invalidate_access(client):
    _, tokens = await register_and_login(client, "logout@example.com", role="STUDENT")
    for _ in range(2):
        response = await client.post(
            "/api/v1/auth/logout",
            json={"refresh_token": tokens["refresh_token"]},
        )
        assert response.status_code == 200
        assert response.json()["data"] == {"revoked": True}

    refresh = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh.status_code == 401
    assert refresh.json()["error"]["code"] == "REFRESH_TOKEN_REVOKED"

    me = await client.get("/api/v1/auth/me", headers=auth_headers(tokens))
    assert me.status_code == 200


async def test_opaque_refresh_cannot_authenticate_as_access(client):
    _, tokens = await register_and_login(client, "opaque@example.com", role="STUDENT")
    response = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {tokens['refresh_token']}"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_ACCESS_TOKEN"
