from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import AppError
from app.models import User, UserRole, UserStatus

bearer_scheme = HTTPBearer(auto_error=False)
SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    request: Request,
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(
            401,
            "AUTHENTICATION_REQUIRED",
            "A valid bearer access token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    payload = request.app.state.token_service.decode_access_token(
        credentials.credentials
    )
    try:
        user_id = uuid.UUID(str(payload["sub"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise AppError(
            401,
            "INVALID_ACCESS_TOKEN",
            "The access token subject is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = await session.scalar(select(User).where(User.id == user_id))
    if user is None or user.status != UserStatus.ACTIVE:
        raise AppError(
            401,
            "USER_INACTIVE",
            "The user account is unavailable",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("role") != user.role.value:
        raise AppError(
            401,
            "INVALID_ACCESS_TOKEN",
            "The access token role is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(role: UserRole) -> Callable[[User], Awaitable[User]]:
    async def dependency(current_user: CurrentUser) -> User:
        if current_user.role != role:
            raise AppError(
                403,
                "ROLE_FORBIDDEN",
                f"This operation requires the {role.value} role",
            )
        return current_user

    return dependency


TeacherUser = Annotated[User, Depends(require_role(UserRole.TEACHER))]
StudentUser = Annotated[User, Depends(require_role(UserRole.STUDENT))]
