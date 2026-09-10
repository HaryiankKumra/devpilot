"""Application configuration.

All configuration is read from the environment (or a local `.env` file) into a
single immutable `Settings` object. Nothing in the codebase reads `os.environ`
directly, which gives us one place to document, validate and override config --
and makes it impossible to accidentally ship a hardcoded secret.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import (
    Field,
    PostgresDsn,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Obvious, greppable placeholder. Production refuses to start with this value.
DEVELOPMENT_SECRET_KEY = "insecure-development-secret-key-do-not-use-in-production"

# Below this length an HS256 key is brute-forceable offline from a single token.
MINIMUM_SECRET_KEY_LENGTH = 32


class LLMMode(StrEnum):
    """How DevPilot reaches a language model.

    `mock` returns a fixed, deterministic review so the whole pipeline runs to
    completion with no API key and no spend. `live` calls the real provider.
    """

    LIVE = "live"
    MOCK = "mock"


class EmbeddingMode(StrEnum):
    """How DevPilot produces embeddings.

    `mock` uses deterministic feature hashing: no key, no cost, and vectors
    whose distances genuinely mean something -- lexically, not semantically.
    """

    LIVE = "live"
    MOCK = "mock"


class GitHubMode(StrEnum):
    """How DevPilot talks to GitHub.

    `mock` serves canned responses from an in-process fake, so the whole
    application can be run and tested without registering a GitHub App or
    holding any credential. `live` talks to the real API.
    """

    LIVE = "live"
    MOCK = "mock"


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

    # --- Language model ------------------------------------------------------
    # `mock` needs no credentials and costs nothing; see docs/llm-setup.md.
    llm_mode: LLMMode = LLMMode.MOCK

    anthropic_api_key: SecretStr | None = None
    llm_model: str = "claude-opus-5"

    # Reviewing code rewards reasoning depth, so this sits at the high end.
    # Typed as a literal so a typo is rejected at startup rather than by the
    # provider on the first review of the day.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"

    # Generous enough for a full review with a couple of dozen findings;
    # hitting the cap truncates mid-JSON and wastes the whole call.
    llm_max_output_tokens: int = Field(default=16_000, ge=1_000)
    llm_request_timeout_seconds: float = Field(default=180.0, gt=0)

    # Findings below this are stored but never posted to GitHub. A reviewer that
    # posts its own guesses is one people stop reading.
    llm_min_confidence_to_post: float = Field(default=0.7, ge=0.0, le=1.0)

    # How many times to re-ask when the model returns something unusable.
    # Generation is stochastic, so a second attempt genuinely can succeed --
    # but a third rarely adds anything a second did not.
    llm_max_validation_retries: int = Field(default=2, ge=0, le=5)

    @property
    def llm_is_mocked(self) -> bool:
        return self.llm_mode is LLMMode.MOCK

    # --- Embeddings ----------------------------------------------------------
    # `mock` needs no credentials; see docs/llm-setup.md.
    embedding_mode: EmbeddingMode = EmbeddingMode.MOCK

    # Anthropic has no embeddings endpoint, so retrieval uses Voyage AI.
    voyage_api_key: SecretStr | None = None
    # Trained on code specifically: a general text embedder retrieves on
    # comments and identifier spelling rather than on what the code does.
    embedding_model: str = "voyage-code-3"

    # Must match the `code_chunks.embedding` column exactly. pgvector fixes
    # dimensionality at the column, so changing this needs a migration and a
    # full re-index -- comparing vectors from two models is meaningless.
    embedding_dimensions: int = Field(default=1024, ge=1)
    embedding_request_timeout_seconds: float = Field(default=60.0, gt=0)

    # --- Retrieval -----------------------------------------------------------
    # How many chunks to put in the prompt. More context is not automatically
    # better: past a handful, the relevant chunk competes for attention with
    # near-misses.
    retrieval_top_k: int = Field(default=6, ge=1, le=50)
    # Cosine distance above this means "not actually related". Without a floor,
    # a query always retrieves its k nearest chunks even when none are relevant.
    retrieval_max_distance: float = Field(default=0.75, ge=0.0, le=2.0)

    # Cap on files pulled from GitHub when indexing a repository.
    max_indexed_files: int = Field(default=1_000, ge=1)

    @property
    def embedding_is_mocked(self) -> bool:
        return self.embedding_mode is EmbeddingMode.MOCK

    # --- Rate limiting -------------------------------------------------------
    rate_limit_enabled: bool = True
    # Generous for normal use; the point is to stop a runaway client, not to
    # meter usage.
    rate_limit_requests: int = Field(default=300, ge=1)
    # Tight, because this is where credential stuffing happens.
    rate_limit_auth_requests: int = Field(default=10, ge=1)
    rate_limit_window_seconds: int = Field(default=60, ge=1)

    # `X-Forwarded-For` is trivially spoofed by the client, so it is honoured
    # only when the deployment sits behind a proxy that overwrites it. Leaving
    # this on without such a proxy lets anyone reset their own rate limit.
    trust_proxy_headers: bool = False

    # --- Security headers ----------------------------------------------------
    # HSTS is only meaningful over HTTPS, and setting it in local development
    # would pin `localhost` to https in the browser for a year.
    enable_hsts: bool = False

    # --- Publishing ----------------------------------------------------------
    # The master switch for writing to someone else's repository. Off by
    # default: a misconfigured deployment should be silent, not chatty on
    # somebody's pull request.
    post_reviews_to_github: bool = False

    # --- Review pipeline limits ----------------------------------------------
    # These bound cost and usefulness, not just resource use. A diff of many
    # thousands of lines produces a prompt no model reads carefully, and the
    # review that comes back is confidently vague -- refusing is more honest.
    max_diff_bytes: int = Field(default=500_000, ge=1_000)
    max_changed_files: int = Field(default=100, ge=1)
    # Files above this are almost always generated, vendored or minified;
    # analysing them yields findings about code nobody wrote by hand.
    max_file_bytes: int = Field(default=200_000, ge=1_000)

    # --- Background workers --------------------------------------------------
    # Celery uses the same Redis instance as the rest of the application but a
    # separate logical database, so flushing one never destroys the other.
    celery_broker_db: int = Field(default=1, ge=0, le=15)
    celery_result_db: int = Field(default=2, ge=0, le=15)

    # A review involves several network round trips and an LLM call. The soft
    # limit raises an exception the task can catch and record; the hard limit
    # kills the worker process, so it sits above the soft one as a backstop for
    # a task wedged somewhere uninterruptible.
    celery_task_soft_time_limit_seconds: int = Field(default=600, ge=1)
    celery_task_time_limit_seconds: int = Field(default=660, ge=2)

    # Retry backoff for transient failures: 30s, 60s, 120s, ... capped.
    celery_retry_backoff_seconds: int = Field(default=30, ge=1)
    celery_retry_backoff_max_seconds: int = Field(default=600, ge=1)

    # How many tasks a worker takes at once. Kept at 1 because review tasks are
    # long and uneven: prefetching several would leave them queued behind one
    # slow job while other workers sit idle.
    celery_prefetch_multiplier: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def _check_time_limits_ordered(self) -> Settings:
        """The hard kill must come after the catchable warning, or it is pointless."""
        if self.celery_task_time_limit_seconds <= self.celery_task_soft_time_limit_seconds:
            raise ValueError(
                "DEVPILOT_CELERY_TASK_TIME_LIMIT_SECONDS must exceed "
                "DEVPILOT_CELERY_TASK_SOFT_TIME_LIMIT_SECONDS, otherwise the task is "
                "killed before it can record why it timed out."
            )
        return self

    def redis_url_for_db(self, db: int) -> str:
        """Return the Redis URL with its database number replaced."""
        base = str(self.redis_url).rstrip("/")
        # A Redis DSN is redis://host:port/<db>; swap the final path segment.
        head, _, tail = base.rpartition("/")
        if tail.isdigit():
            return f"{head}/{db}"
        return f"{base}/{db}"

    @property
    def celery_broker_url(self) -> str:
        return self.redis_url_for_db(self.celery_broker_db)

    @property
    def celery_result_backend(self) -> str:
        return self.redis_url_for_db(self.celery_result_db)

    # --- GitHub integration --------------------------------------------------
    # `mock` needs no credentials at all; see docs/github-app-setup.md for what
    # `live` requires and how to obtain it.
    github_mode: GitHubMode = GitHubMode.MOCK

    # From the GitHub App's settings page.
    github_app_id: str | None = None
    github_app_slug: str | None = Field(
        default=None, description="URL name of the app, used to build install links."
    )

    # The App's RSA private key, used to sign the short-lived JWT that is
    # exchanged for an installation token. Supply the PEM inline, or point at a
    # file -- a path is usually easier to manage as a mounted secret.
    github_app_private_key: SecretStr | None = None
    github_app_private_key_path: Path | None = None

    # Verifies the HMAC on incoming webhooks. Without it every webhook is
    # rejected, which is the correct default: an unverified webhook is
    # unauthenticated input that can create work and post comments.
    github_webhook_secret: SecretStr | None = None

    # OAuth credentials, used to link a DevPilot account to a GitHub identity.
    github_client_id: str | None = None
    github_client_secret: SecretStr | None = None
    # Where GitHub sends the user back after they authorise. Must exactly match
    # the callback URL registered on the app.
    github_oauth_redirect_uri: str = "http://localhost:8000/api/v1/github/callback"
    # Where the browser lands once linking finishes.
    frontend_base_url: str = "http://localhost:5173"

    github_api_url: str = "https://api.github.com"
    github_web_url: str = "https://github.com"
    github_request_timeout_seconds: float = Field(default=10.0, gt=0)

    @property
    def github_is_mocked(self) -> bool:
        return self.github_mode is GitHubMode.MOCK

    def resolve_github_private_key(self) -> str | None:
        """Return the App private key PEM, from whichever source is configured.

        The inline value wins so an environment variable can override a file
        baked into an image.
        """
        if self.github_app_private_key is not None:
            # Escaped newlines are near-unavoidable when a PEM travels through
            # a `.env` file or a CI secret, so accept both spellings.
            return self.github_app_private_key.get_secret_value().replace("\\n", "\n")
        if self.github_app_private_key_path is not None:
            return self.github_app_private_key_path.read_text(encoding="utf-8")
        return None

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
