"""Tests for settings parsing and environment-dependent behaviour."""

from __future__ import annotations

from typing import Any

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


class TestGitHubPrivateKeyResolution:
    """Where the App private key comes from, and what counts as "not set".

    Untested until a real GitHub App was configured and the key silently
    resolved to an empty string, which surfaced later as GitHub rejecting the
    credentials rather than as a configuration error.
    """

    PEM = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----\n"

    def test_returns_none_when_nothing_is_configured(self) -> None:
        assert Settings(_env_file=None).resolve_github_private_key() is None

    def test_reads_an_inline_key(self) -> None:
        loaded = Settings(_env_file=None, github_app_private_key=SecretStr(self.PEM))

        assert loaded.resolve_github_private_key() == self.PEM

    def test_unescapes_newlines_in_an_inline_key(self) -> None:
        """A real PEM rarely survives a .env file with literal newlines."""
        loaded = Settings(
            _env_file=None,
            github_app_private_key=SecretStr(self.PEM.replace("\n", "\n")),
        )

        assert loaded.resolve_github_private_key() == self.PEM

    def test_reads_a_key_file(self, tmp_path: Any) -> None:
        key_file = tmp_path / "app.pem"
        key_file.write_text(self.PEM, encoding="utf-8")

        loaded = Settings(_env_file=None, github_app_private_key_path=key_file)

        assert loaded.resolve_github_private_key() == self.PEM

    def test_a_blank_inline_key_falls_through_to_the_file(self, tmp_path: Any) -> None:
        """`.env.example` ships the inline variable present and empty, so this
        is the *documented* path -- an empty SecretStr must not win over it."""
        key_file = tmp_path / "app.pem"
        key_file.write_text(self.PEM, encoding="utf-8")

        loaded = Settings(
            _env_file=None,
            github_app_private_key=SecretStr(""),
            github_app_private_key_path=key_file,
        )

        assert loaded.resolve_github_private_key() == self.PEM

    def test_a_whitespace_inline_key_also_falls_through(self, tmp_path: Any) -> None:
        key_file = tmp_path / "app.pem"
        key_file.write_text(self.PEM, encoding="utf-8")

        loaded = Settings(
            _env_file=None,
            github_app_private_key=SecretStr("   \n  "),
            github_app_private_key_path=key_file,
        )

        assert loaded.resolve_github_private_key() == self.PEM

    def test_a_real_inline_key_still_beats_the_file(self, tmp_path: Any) -> None:
        """The override exists so an env var can beat a key baked into an image."""
        key_file = tmp_path / "app.pem"
        key_file.write_text("from-the-file", encoding="utf-8")

        loaded = Settings(
            _env_file=None,
            github_app_private_key=SecretStr(self.PEM),
            github_app_private_key_path=key_file,
        )

        assert loaded.resolve_github_private_key() == self.PEM

    def test_a_missing_file_names_the_path_it_tried(self, tmp_path: Any) -> None:
        """Inside a container this is nearly always a host path never mounted."""
        missing = tmp_path / "nope.pem"
        loaded = Settings(_env_file=None, github_app_private_key_path=missing)

        with pytest.raises(ValueError, match="does not exist"):
            loaded.resolve_github_private_key()
