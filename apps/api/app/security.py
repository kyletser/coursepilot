from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import Settings
from app.errors import AppError
from app.models import User


class PasswordService:
    def __init__(self, settings: Settings) -> None:
        self._hasher = PasswordHasher(
            time_cost=settings.argon2_time_cost,
            memory_cost=settings.argon2_memory_cost,
            parallelism=settings.argon2_parallelism,
            hash_len=32,
            salt_len=16,
            type=Type.ID,
        )
        self._dummy_hash = self._hasher.hash("coursepilot-dummy-password")

    def hash(self, password: str) -> str:
        return self._hasher.hash(password)

    def verify(self, password_hash: str, password: str) -> bool:
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            return False

    def verify_dummy(self, password: str) -> None:
        self.verify(self._dummy_hash, password)

    def needs_rehash(self, password_hash: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return False


class TokenService:
    algorithm = "HS256"

    def __init__(self, settings: Settings) -> None:
        self._secret = settings.jwt_secret
        self._issuer = settings.jwt_issuer
        self._audience = settings.jwt_audience
        self._access_ttl = timedelta(minutes=settings.access_token_minutes)
        self.refresh_ttl = timedelta(days=settings.refresh_token_days)

    @property
    def access_expires_in(self) -> int:
        return int(self._access_ttl.total_seconds())

    def create_access_token(self, user: User) -> str:
        now = datetime.now(UTC)
        payload: dict[str, Any] = {
            "sub": str(user.id),
            "role": user.role.value,
            "type": "access",
            "jti": str(uuid.uuid4()),
            "iss": self._issuer,
            "aud": self._audience,
            "iat": now,
            "nbf": now,
            "exp": now + self._access_ttl,
        }
        return jwt.encode(payload, self._secret, algorithm=self.algorithm)

    def decode_access_token(self, token: str) -> dict[str, Any]:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self.algorithm],
                audience=self._audience,
                issuer=self._issuer,
                options={
                    "require": [
                        "sub",
                        "role",
                        "type",
                        "jti",
                        "iss",
                        "aud",
                        "iat",
                        "nbf",
                        "exp",
                    ]
                },
            )
        except jwt.PyJWTError as exc:
            raise AppError(
                401,
                "INVALID_ACCESS_TOKEN",
                "The access token is invalid or expired",
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        if payload.get("type") != "access":
            raise AppError(
                401,
                "INVALID_ACCESS_TOKEN",
                "The access token type is invalid",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return payload

    @staticmethod
    def create_refresh_token() -> str:
        return f"cp_rt_{secrets.token_urlsafe(48)}"


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
