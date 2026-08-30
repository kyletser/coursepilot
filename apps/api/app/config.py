from __future__ import annotations

from functools import lru_cache
from pathlib import Path

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
    environment: str = "development"
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
    readiness_timeout_seconds: float = Field(default=3.0, gt=0, le=30)
    readiness_check_migrations: bool = True
    readiness_check_redis: bool = True
    readiness_check_neo4j: bool = True
    readiness_check_llm: bool = False
    readiness_check_index: bool = True
    index_root: Path = Path("/data/indexes")

    sql_echo: bool = False

    @model_validator(mode="after")
    def validate_production_secrets(self) -> Settings:
        if self.environment.lower() == "production":
            if self.jwt_secret == "coursepilot-development-only-change-this-secret":
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
