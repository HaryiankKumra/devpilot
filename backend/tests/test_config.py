"""Tests for settings parsing and environment-dependent behaviour."""

from __future__ import annotations

import pytest

from app.core.config import Environment, Settings, get_settings
from app.main import create_app


class TestSettings:
    def test_reads_prefixed_environment_variables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEVPILOT_ENVIRONMENT", "production")
        monkeypatch.setenv("DEVPILOT_LOG_LEVEL", "WARNING")

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
        app = create_app(Settings(environment=Environment.PRODUCTION))

        assert app.docs_url is None
        assert app.redoc_url is None
        assert app.openapi_url is None
