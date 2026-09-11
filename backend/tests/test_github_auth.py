"""Tests for GitHub App authentication: the app JWT and the token cache."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.github.auth import (
    APP_JWT_LIFETIME_SECONDS,
    TOKEN_REFRESH_MARGIN,
    CachedToken,
    InstallationTokenCache,
    build_app_jwt,
)
from app.integrations.github.exceptions import GitHubConfigurationError
from app.integrations.github.models import GitHubInstallationToken

APP_ID = "123456"


@pytest.fixture(scope="module")
def rsa_keypair() -> tuple[str, str]:
    """A throwaway RSA key, generated so no key material lives in the repository."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return private_pem, public_pem


@pytest.fixture
def github_settings(rsa_keypair: tuple[str, str]) -> Settings:
    private_pem, _ = rsa_keypair
    return Settings(
        _env_file=None,
        github_app_id=APP_ID,
        github_app_private_key=SecretStr(private_pem),
    )


class TestBuildAppJwt:
    def test_signs_a_token_the_public_key_verifies(
        self, github_settings: Settings, rsa_keypair: tuple[str, str]
    ) -> None:
        _, public_pem = rsa_keypair

        token = build_app_jwt(github_settings)

        claims = jwt.decode(token, public_pem, algorithms=["RS256"])
        assert claims["iss"] == APP_ID

    def test_uses_rs256_not_a_symmetric_algorithm(self, github_settings: Settings) -> None:
        """GitHub only accepts RS256 for app JWTs."""
        header = jwt.get_unverified_header(build_app_jwt(github_settings))

        assert header["alg"] == "RS256"

    def test_expires_within_githubs_ten_minute_limit(
        self, github_settings: Settings, rsa_keypair: tuple[str, str]
    ) -> None:
        """GitHub rejects app JWTs valid for more than ten minutes."""
        _, public_pem = rsa_keypair

        claims = jwt.decode(build_app_jwt(github_settings), public_pem, algorithms=["RS256"])

        assert claims["exp"] - claims["iat"] == APP_JWT_LIFETIME_SECONDS
        assert APP_JWT_LIFETIME_SECONDS < 10 * 60

    def test_backdates_issued_at_to_tolerate_clock_skew(
        self, github_settings: Settings, rsa_keypair: tuple[str, str]
    ) -> None:
        """A JWT that looks issued in the future is rejected by GitHub."""
        _, public_pem = rsa_keypair
        before = datetime.now(UTC)

        claims = jwt.decode(build_app_jwt(github_settings), public_pem, algorithms=["RS256"])

        assert claims["iat"] < before.timestamp()

    def test_reports_a_missing_app_id_clearly(self, rsa_keypair: tuple[str, str]) -> None:
        private_pem, _ = rsa_keypair
        settings = Settings(_env_file=None, github_app_private_key=SecretStr(private_pem))

        with pytest.raises(GitHubConfigurationError, match="GITHUB_APP_ID"):
            build_app_jwt(settings)

    def test_reports_a_missing_private_key_clearly(self) -> None:
        with pytest.raises(GitHubConfigurationError, match="private key"):
            build_app_jwt(Settings(_env_file=None, github_app_id=APP_ID))

    def test_reports_an_unusable_private_key_as_configuration(self) -> None:
        """A malformed PEM is a deployment mistake, not a runtime fault."""
        settings = Settings(
            _env_file=None,
            github_app_id=APP_ID,
            github_app_private_key=SecretStr(
                "-----BEGIN PRIVATE KEY-----\nnope\n-----END PRIVATE KEY-----\n"
            ),
        )

        with pytest.raises(GitHubConfigurationError):
            build_app_jwt(settings)

    def test_reports_an_incomplete_private_key_as_configuration(self) -> None:
        """Only the first line of a .pem made it into the environment. The
        loader's message names the cause; it must reach the caller as a
        configuration error (503), not as an unhandled ValueError (500)."""
        settings = Settings(
            _env_file=None,
            github_app_id=APP_ID,
            github_app_private_key=SecretStr("-----BEGIN RSA PRIVATE KEY-----"),
        )

        with pytest.raises(GitHubConfigurationError, match="not a complete PEM"):
            build_app_jwt(settings)

    def test_reads_the_key_from_a_file_when_configured(
        self, rsa_keypair: tuple[str, str], tmp_path: object
    ) -> None:
        """A mounted secret file is usually easier to manage than an env var."""
        from pathlib import Path

        private_pem, public_pem = rsa_keypair
        key_file = Path(str(tmp_path)) / "app.pem"
        key_file.write_text(private_pem, encoding="utf-8")

        settings = Settings(
            _env_file=None, github_app_id=APP_ID, github_app_private_key_path=key_file
        )

        claims = jwt.decode(build_app_jwt(settings), public_pem, algorithms=["RS256"])
        assert claims["iss"] == APP_ID


class TestInstallationTokenCache:
    def _token(self, *, expires_in: timedelta) -> GitHubInstallationToken:
        return GitHubInstallationToken(
            token="ghs_example", expires_at=datetime.now(UTC) + expires_in
        )

    def test_returns_a_stored_token(self) -> None:
        cache = InstallationTokenCache()
        cache.store(1, self._token(expires_in=timedelta(hours=1)))

        assert cache.get(1) == "ghs_example"

    def test_returns_none_for_an_unknown_installation(self) -> None:
        assert InstallationTokenCache().get(999) is None

    def test_discards_a_token_that_is_about_to_expire(self) -> None:
        """Refreshing early stops a request beginning with a token that dies mid-flight."""
        cache = InstallationTokenCache()
        cache.store(1, self._token(expires_in=TOKEN_REFRESH_MARGIN - timedelta(seconds=30)))

        assert cache.get(1) is None

    def test_keeps_a_token_with_time_to_spare(self) -> None:
        cache = InstallationTokenCache()
        cache.store(1, self._token(expires_in=TOKEN_REFRESH_MARGIN + timedelta(minutes=10)))

        assert cache.get(1) is not None

    def test_discards_an_expired_token(self) -> None:
        cache = InstallationTokenCache()
        cache.store(1, self._token(expires_in=timedelta(seconds=-1)))

        assert cache.get(1) is None

    def test_invalidate_forces_a_fresh_token(self) -> None:
        cache = InstallationTokenCache()
        cache.store(1, self._token(expires_in=timedelta(hours=1)))

        cache.invalidate(1)

        assert cache.get(1) is None

    def test_keeps_installations_separate(self) -> None:
        """A token is scoped to one installation; crossing them would leak access."""
        cache = InstallationTokenCache()
        cache.store(1, self._token(expires_in=timedelta(hours=1)))

        assert cache.get(2) is None

    def test_cached_token_usability_is_time_based(self) -> None:
        now = datetime.now(UTC)
        cached = CachedToken(token="t", expires_at=now + timedelta(minutes=30))

        assert cached.is_usable(now=now)
        assert not cached.is_usable(now=now + timedelta(minutes=29))
