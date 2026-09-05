from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "CoursePilot API"
    environment: Literal["development", "test", "production"] = "development"
    debug: bool = False

    database_url: str = (
        "postgresql+asyncpg://coursepilot:coursepilot@postgres:5432/coursepilot"
    )
    redis_url: str = "redis://redis:6379/0"
    neo4j_uri: str = "bolt://neo4j:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "coursepilot-development"

    jwt_secret: str = "coursepilot-development-only-change-this-secret"
    jwt_issuer: str = "coursepilot-api"
    jwt_audience: str = "coursepilot-web"
    access_token_minutes: int = Field(default=30, ge=1, le=1440)
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    invite_ttl_days: int = Field(default=30, ge=1, le=365)

    argon2_time_cost: int = Field(default=3, ge=1, le=10)
    argon2_memory_cost: int = Field(default=65536, ge=8192, le=1048576)
    argon2_parallelism: int = Field(default=4, ge=1, le=16)

    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    embedding_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    model_allow_download: bool = False
    upload_root: Path = Path("/data/uploads")
    index_root: Path = Path("/data/indexes")
    hf_home: Path = Path("/data/models/huggingface")
    max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1, le=50 * 1024 * 1024)
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    readiness_check_migrations: bool = True
    readiness_check_redis: bool = True
    readiness_check_neo4j: bool = True
    readiness_check_llm: bool = False
    readiness_check_index: bool = True

    sql_echo: bool = False

    @property
    def hf_hub_cache(self) -> Path:
        """Canonical Hugging Face Hub cache below ``HF_HOME``."""

        return self.hf_home / "hub"

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment == "production":
            if self.jwt_secret in {
                "coursepilot-development-only-change-this-secret",
                "replace-with-a-random-string-at-least-32-bytes-long",
            }:
                raise ValueError("JWT_SECRET must be changed in production")
            if len(self.jwt_secret.encode("utf-8")) < 32:
                raise ValueError("JWT_SECRET must be at least 32 bytes in production")
        if self.readiness_check_llm and not (self.llm_base_url and self.llm_api_key):
            raise ValueError(
                "LLM_BASE_URL and LLM_API_KEY are required when READINESS_CHECK_LLM=true"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
