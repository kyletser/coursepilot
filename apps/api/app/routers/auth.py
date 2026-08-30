from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool

from app.dependencies import CurrentUser, SessionDep
from app.errors import AppError, success_response
from app.models import RefreshToken, User, UserStatus
from app.schemas import (
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.security import ensure_aware, hash_secret

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
TOKEN_RESPONSE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _token_response(
    request: Request,
    user: User,
    refresh_token: str,
    refresh_record: RefreshToken,
) -> TokenResponse:
    token_service = request.app.state.token_service
    return TokenResponse(
        access_token=token_service.create_access_token(user),
        refresh_token=refresh_token,
        expires_in=token_service.access_expires_in,
        refresh_expires_in=max(
            0,
            int(
                (
                    ensure_aware(refresh_record.expires_at) - datetime.now(UTC)
                ).total_seconds()
            ),
        ),
    )


def _new_refresh_record(
    request: Request,
    user: User,
    *,
    family_id: uuid.UUID | None = None,
    parent_id: uuid.UUID | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, RefreshToken]:
    token_service = request.app.state.token_service
    plaintext = token_service.create_refresh_token()
    now = datetime.now(UTC)
    record = RefreshToken(
        user_id=user.id,
        token_hash=hash_secret(plaintext),
        family_id=family_id or uuid.uuid4(),
        parent_id=parent_id,
        expires_at=expires_at or (now + token_service.refresh_ttl),
    )
    return plaintext, record


@router.post("/register", status_code=201)
async def register(payload: RegisterRequest, request: Request, session: SessionDep):
    existing = await session.scalar(select(User.id).where(User.email == payload.email))
    if existing is not None:
        raise AppError(
            409,
            "EMAIL_ALREADY_REGISTERED",
            "An account with this email already exists",
        )

    user = User(
        email=str(payload.email),
        password_hash=await run_in_threadpool(
            request.app.state.password_service.hash, payload.password
        ),
        role=payload.role,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise AppError(
            409,
            "EMAIL_ALREADY_REGISTERED",
            "An account with this email already exists",
        ) from exc
    await session.refresh(user)
    return success_response(request, UserResponse.model_validate(user), status_code=201)


@router.post("/login")
async def login(payload: LoginRequest, request: Request, session: SessionDep):
    user = await session.scalar(select(User).where(User.email == payload.email))
    password_service = request.app.state.password_service
    if user is None:
        await run_in_threadpool(password_service.verify_dummy, payload.password)
        raise AppError(401, "INVALID_CREDENTIALS", "Email or password is incorrect")
    if not await run_in_threadpool(
        password_service.verify, user.password_hash, payload.password
    ):
        raise AppError(401, "INVALID_CREDENTIALS", "Email or password is incorrect")
    if user.status != UserStatus.ACTIVE:
        raise AppError(403, "USER_INACTIVE", "The user account is inactive")

    if password_service.needs_rehash(user.password_hash):
        user.password_hash = await run_in_threadpool(
            password_service.hash, payload.password
        )
    plaintext, record = _new_refresh_record(request, user)
    session.add(record)
    await session.commit()
    return success_response(
        request,
        _token_response(request, user, plaintext, record),
        headers=TOKEN_RESPONSE_HEADERS,
    )


@router.post("/refresh")
async def refresh(payload: RefreshRequest, request: Request, session: SessionDep):
    token_hash = hash_secret(payload.refresh_token)
    record = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == token_hash)
        .with_for_update()
    )
    if record is None:
        raise AppError(401, "INVALID_REFRESH_TOKEN", "The refresh token is invalid")

    now = datetime.now(UTC)
    if record.consumed_at is not None:
        await session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.family_id == record.family_id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        # Commit before raising: rolling back here would leave a stolen child token alive.
        await session.commit()
        raise AppError(
            401,
            "REFRESH_TOKEN_REUSE_DETECTED",
            "Refresh token reuse was detected; the token family was revoked",
        )
    if record.revoked_at is not None:
        raise AppError(401, "REFRESH_TOKEN_REVOKED", "The refresh token was revoked")
    if ensure_aware(record.expires_at) <= now:
        record.revoked_at = now
        await session.commit()
        raise AppError(401, "REFRESH_TOKEN_EXPIRED", "The refresh token expired")

    user = await session.scalar(select(User).where(User.id == record.user_id))
    if user is None or user.status != UserStatus.ACTIVE:
        record.revoked_at = now
        await session.commit()
        raise AppError(401, "USER_INACTIVE", "The user account is unavailable")

    record.consumed_at = now
    record.revoked_at = now
    plaintext, child = _new_refresh_record(
        request,
        user,
        family_id=record.family_id,
        parent_id=record.id,
        expires_at=record.expires_at,
    )
    session.add(child)
    await session.commit()
    return success_response(
        request,
        _token_response(request, user, plaintext, child),
        headers=TOKEN_RESPONSE_HEADERS,
    )


@router.post("/logout")
async def logout(payload: LogoutRequest, request: Request, session: SessionDep):
    record = await session.scalar(
        select(RefreshToken)
        .where(RefreshToken.token_hash == hash_secret(payload.refresh_token))
        .with_for_update()
    )
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.now(UTC)
        await session.commit()
    # Idempotent and non-enumerating: unknown/already-revoked tokens receive the same result.
    return success_response(request, {"revoked": True})


@router.get("/me")
async def me(request: Request, current_user: CurrentUser):
    return success_response(request, UserResponse.model_validate(current_user))
