from __future__ import annotations

import uuid


def assert_envelope(body: dict) -> None:
    assert set(body) == {"data", "error", "request_id"}
    uuid.UUID(body["request_id"])


async def test_live_and_ready_use_unified_envelope(client):
    live = await client.get("/health/live")
    assert live.status_code == 200
    assert_envelope(live.json())
    assert live.json()["data"] == {"status": "ok"}
    assert live.headers["X-Request-ID"] == live.json()["request_id"]

    ready = await client.get("/health/ready")
    assert ready.status_code == 200
    assert_envelope(ready.json())
    assert ready.json()["data"]["components"]["database"]["status"] == "ok"
    assert ready.json()["data"]["components"]["redis"]["status"] == "skipped"


async def test_validation_and_routing_errors_are_wrapped_without_secrets(client):
    password = "short-secret-value"
    invalid = await client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": password, "role": "ROOT"},
    )
    assert invalid.status_code == 422
    assert_envelope(invalid.json())
    assert invalid.json()["data"] is None
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"
    assert password not in invalid.text

    missing = await client.get("/does-not-exist")
    assert missing.status_code == 404
    assert_envelope(missing.json())
    assert missing.json()["error"]["code"] == "HTTP_ERROR"


async def test_missing_auth_is_unified(client):
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
