from __future__ import annotations

import httpx


async def register(
    client: httpx.AsyncClient,
    email: str,
    *,
    role: str,
    password: str = "correct horse battery staple",
) -> dict:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "role": role},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


async def login(
    client: httpx.AsyncClient,
    email: str,
    *,
    password: str = "correct horse battery staple",
) -> dict:
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": password}
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


async def register_and_login(
    client: httpx.AsyncClient,
    email: str,
    *,
    role: str,
    password: str = "correct horse battery staple",
) -> tuple[dict, dict]:
    user = await register(client, email, role=role, password=password)
    tokens = await login(client, email, password=password)
    return user, tokens


def auth_headers(tokens: dict) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


async def create_course(
    client: httpx.AsyncClient,
    tokens: dict,
    *,
    template: str = "DATA_STRUCTURES",
    code: str = "CS201",
    name: str = "Data Structures",
) -> dict:
    response = await client.post(
        "/api/v1/courses",
        headers=auth_headers(tokens),
        json={
            "template": template,
            "code": code,
            "name": name,
            "description": "A course",
            "semester": "2026-Fall",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]
