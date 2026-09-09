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

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Obvious, greppable placeholder. Production refuses to start with this value.
DEVELOPMENT_SECRET_KEY = "insecure-development-secret-key-do-not-use-in-production"

# Below this length an HS256 key is brute-forceable offline from a single token.
MINIMUM_SECRET_KEY_LENGTH = 32


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

    # --- Authentication ------------------------------------------------------
    # Signs and verifies access tokens. The placeholder below only ever applies
    # outside production -- `_reject_insecure_production_secret` refuses to boot
    # a production process that is still using it, because a predictable signing
    # key lets anyone mint a token for any account.
    secret_key: SecretStr = Field(default=SecretStr(DEVELOPMENT_SECRET_KEY))
    jwt_algorithm: str = Field(default="HS256")
    # Short enough to limit the damage of a leaked token, long enough not to
    # interrupt a working session. Access tokens cannot be revoked before they
    # expire; see docs/engineering-tradeoffs.md.
    access_token_expire_minutes: int = Field(default=60, ge=1)

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

    @model_validator(mode="after")
    def _reject_insecure_production_secret(self) -> Settings:
        """Fail fast rather than run production with a guessable signing key.

        A misconfigured secret is invisible at runtime -- everything works --
        while allowing anyone who knows the default to forge a token for any
        account. Refusing to boot converts a silent compromise into an obvious
        deployment error.
        """
        if not self.is_production:
            return self

        secret = self.secret_key.get_secret_value()
        if secret == DEVELOPMENT_SECRET_KEY:
            raise ValueError(
                "DEVPILOT_SECRET_KEY is still the development placeholder. "
                'Generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        if len(secret) < MINIMUM_SECRET_KEY_LENGTH:
            raise ValueError(
                f"DEVPILOT_SECRET_KEY must be at least {MINIMUM_SECRET_KEY_LENGTH} "
                f"characters in production; got {len(secret)}."
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Cached so that every import path sees the same object and `.env` is parsed
    once. Tests clear the cache via `get_settings.cache_clear()`.
    """
    return Settings()
