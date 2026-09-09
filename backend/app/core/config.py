"""Application configuration.

All configuration is read from the environment (or a local `.env` file) into a
single immutable `Settings` object. Nothing in the codebase reads `os.environ`
directly, which gives us one place to document, validate and override config --
and makes it impossible to accidentally ship a hardcoded secret.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    """Deployment environment. Drives log format and error verbosity."""

    LOCAL = "local"
    CI = "ci"
    PRODUCTION = "production"


class LogLevel(StrEnum):
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class Settings(BaseSettings):
    """Runtime settings, populated from `DEVPILOT_*` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="DEVPILOT_",
        env_file=".env",
        env_file_encoding="utf-8",
        # Compose and CI inject many unrelated variables; ignore rather than fail.
        extra="ignore",
        frozen=True,
    )

    # --- Application ---------------------------------------------------------
    environment: Environment = Environment.LOCAL
    debug: bool = False
    log_level: LogLevel = LogLevel.INFO

    project_name: str = "DevPilot"
    version: str = "0.1.0"
    api_v1_prefix: str = "/api/v1"

    # --- Backing services ----------------------------------------------------
    database_url: PostgresDsn = Field(
        default=PostgresDsn("postgresql+psycopg://devpilot:devpilot@localhost:5432/devpilot"),
    )
    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))

    # Connection-pool tuning. The timeouts matter more than the sizes: they are
    # what turns "the database is down" into a fast 503 instead of a hung
    # request that ties up a worker thread.
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_pool_timeout_seconds: int = Field(default=5, ge=1)
    db_connect_timeout_seconds: int = Field(default=3, ge=1)
    db_statement_timeout_ms: int = Field(default=10_000, ge=100)

    redis_socket_timeout_seconds: int = Field(default=2, ge=1)

    # --- HTTP ----------------------------------------------------------------
    # `NoDecode` suppresses pydantic-settings' default JSON decoding for complex
    # types, which would otherwise reject `a,b` before the validator below runs.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        """Accept `a,b` as well as a JSON list, so `.env` files stay readable."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that every import path sees the same object and `.env` is parsed
    once. Tests clear the cache via `get_settings.cache_clear()`.
    """
    return Settings()
