"""Tests for settings parsing and environment-dependent behaviour."""

from __future__ import annotations

import pytest
from pydantic import SecretStr

from app.core.config import (
    DEVELOPMENT_SECRET_KEY,
    Environment,
    Settings,
    get_settings,
)
from app.main import create_app


class TestSettings:
    def test_reads_prefixed_environment_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVPILOT_ENVIRONMENT", "production")
        monkeypatch.setenv("DEVPILOT_LOG_LEVEL", "WARNING")
        # Production refuses to start on the placeholder secret, so supply one.
        monkeypatch.setenv("DEVPILOT_SECRET_KEY", "x" * 48)

        loaded = Settings(_env_file=None)

        assert loaded.environment is Environment.PRODUCTION
        assert loaded.log_level.value == "WARNING"
        assert loaded.is_production

    def test_parses_comma_separated_cors_origins(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`.env` files are easier to read with plain commas than JSON arrays."""
        monkeypatch.setenv(
            "DEVPILOT_CORS_ORIGINS", "http://localhost:5173, https://devpilot.example"
        )

        loaded = Settings(_env_file=None)

        assert loaded.cors_origins == [
            "http://localhost:5173",
            "https://devpilot.example",
        ]

    def test_rejects_an_unknown_log_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVPILOT_LOG_LEVEL", "LOUD")

        with pytest.raises(ValueError):
            Settings(_env_file=None)

    def test_is_cached_per_process(self) -> None:
        assert get_settings() is get_settings()


class TestDocsExposure:
    def test_interactive_docs_are_served_outside_production(self, settings: Settings) -> None:
        app = create_app(settings)

        assert app.openapi_url == "/openapi.json"

    def test_interactive_docs_are_disabled_in_production(self) -> None:
        """The schema advertises every route, so production must not serve it."""
        app = create_app(
            Settings(environment=Environment.PRODUCTION, secret_key=SecretStr("x" * 48))
        )

        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None


class TestProductionSecretGuard:
    """A predictable signing key lets anyone mint a token for any account."""

    def test_production_refuses_the_development_placeholder(self) -> None:
        with pytest.raises(ValueError, match="development placeholder"):
            Settings(environment=Environment.PRODUCTION, _env_file=None)

    def test_production_refuses_a_short_secret(self) -> None:
        with pytest.raises(ValueError, match="at least"):
            Settings(
                environment=Environment.PRODUCTION,
                secret_key=SecretStr("too-short"),
                _env_file=None,
            )

    def test_production_accepts_a_strong_secret(self) -> None:
        loaded = Settings(
            environment=Environment.PRODUCTION,
            secret_key=SecretStr("x" * 48),
            _env_file=None,
        )

        assert loaded.is_production

    def test_non_production_tolerates_the_placeholder(self) -> None:
        """Local development must not require secret generation to start."""
        loaded = Settings(environment=Environment.LOCAL, _env_file=None)

        assert loaded.secret_key.get_secret_value() == DEVELOPMENT_SECRET_KEY

    def test_the_secret_is_not_exposed_by_repr(self) -> None:
        """`SecretStr` keeps the key out of logs and tracebacks."""
        loaded = Settings(secret_key=SecretStr("super-secret-value"), _env_file=None)

        assert "super-secret-value" not in repr(loaded)
