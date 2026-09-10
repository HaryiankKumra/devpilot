"""Tests for the REST GitHub client.

These use `httpx.MockTransport`, so the real client code runs -- request
building, header construction, pagination, error mapping -- against scripted
responses instead of the network. The mapping from GitHub's status codes to
DevPilot exceptions is the point: everything above this layer decides whether to
retry based purely on which exception it caught.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr

from app.core.config import Settings
from app.integrations.github.auth import InstallationTokenCache
from app.integrations.github.client import GITHUB_API_VERSION, RestGitHubClient
from app.integrations.github.exceptions import (
    GitHubAuthenticationError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubTransientError,
)

INSTALLATION_ID = 555


@pytest.fixture(scope="module")
def private_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()


@pytest.fixture
def github_settings(private_pem: str) -> Settings:
    return Settings(
        _env_file=None,
        github_app_id="123456",
        github_app_private_key=SecretStr(private_pem),
        github_client_id="Iv1.example",
        github_client_secret=SecretStr("client-secret"),
    )


def make_client(
    github_settings: Settings,
    handler: object,
    *,
    prime_token: bool = True,
) -> RestGitHubClient:
    """Build a client whose HTTP calls are served by `handler`.

    `prime_token` seeds the installation-token cache so tests of ordinary API
    calls do not each have to script the token exchange as well.
    """
    cache = InstallationTokenCache()
    if prime_token:
        from app.integrations.github.models import GitHubInstallationToken

        cache.store(
            INSTALLATION_ID,
            GitHubInstallationToken(
                token="ghs_cached", expires_at=datetime.now(UTC) + timedelta(hours=1)
            ),
        )
    return RestGitHubClient(
        github_settings,
        token_cache=cache,
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
    )


def json_response(payload: object, status_code: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(status_code, json=payload, headers=headers)


class TestErrorMapping:
    """Every GitHub failure becomes an exception that says whether to retry."""

    @pytest.mark.parametrize(
        ("status_code", "expected"),
        [
            (401, GitHubAuthenticationError),
            (403, GitHubAuthenticationError),
            (404, GitHubNotFoundError),
            (422, GitHubResponseError),
            (500, GitHubTransientError),
            (502, GitHubTransientError),
            (503, GitHubTransientError),
        ],
    )
    def test_status_codes_map_to_exceptions(
        self, github_settings: Settings, status_code: int, expected: type[Exception]
    ) -> None:
        client = make_client(github_settings, lambda request: httpx.Response(status_code, json={}))

        with pytest.raises(expected):
            client.get_repository(INSTALLATION_ID, "owner/name")

    def test_a_timeout_is_transient(self, github_settings: Settings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("timed out", request=request)

        client = make_client(github_settings, handler)

        with pytest.raises(GitHubTransientError, match="timed out"):
            client.get_repository(INSTALLATION_ID, "owner/name")

    def test_a_connection_failure_is_transient(self, github_settings: Settings) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        client = make_client(github_settings, handler)

        with pytest.raises(GitHubTransientError):
            client.get_repository(INSTALLATION_ID, "owner/name")


class TestRateLimiting:
    def test_exhausted_limit_is_not_mistaken_for_a_permission_error(
        self, github_settings: Settings
    ) -> None:
        """GitHub signals both with 403; the remaining-count header separates them."""
        reset_at = int((datetime.now(UTC) + timedelta(seconds=90)).timestamp())
        client = make_client(
            github_settings,
            lambda request: httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(reset_at)},
            ),
        )

        with pytest.raises(GitHubRateLimitError) as caught:
            client.get_repository(INSTALLATION_ID, "owner/name")

        # Roughly 90 seconds, allowing for the clock ticking during the test.
        assert 80 <= caught.value.retry_after_seconds <= 90

    def test_a_genuine_403_is_still_an_authentication_error(
        self, github_settings: Settings
    ) -> None:
        client = make_client(
            github_settings,
            lambda request: httpx.Response(
                403,
                json={"message": "Resource not accessible by integration"},
                headers={"x-ratelimit-remaining": "4999"},
            ),
        )

        with pytest.raises(GitHubAuthenticationError):
            client.get_repository(INSTALLATION_ID, "owner/name")

    def test_honours_an_explicit_retry_after(self, github_settings: Settings) -> None:
        client = make_client(
            github_settings,
            lambda request: httpx.Response(429, json={}, headers={"retry-after": "42"}),
        )

        with pytest.raises(GitHubRateLimitError) as caught:
            client.get_repository(INSTALLATION_ID, "owner/name")

        assert caught.value.retry_after_seconds == 42


class TestRequestConstruction:
    def test_sends_the_installation_token_and_pinned_api_version(
        self, github_settings: Settings
    ) -> None:
        seen: dict[str, str] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.update(request.headers)
            return json_response(
                {"id": 1, "name": "n", "full_name": "owner/name", "default_branch": "main"}
            )

        make_client(github_settings, handler).get_repository(INSTALLATION_ID, "owner/name")

        assert seen["authorization"] == "Bearer ghs_cached"
        assert seen["x-github-api-version"] == GITHUB_API_VERSION
        assert seen["accept"] == "application/vnd.github+json"

    def test_mints_an_installation_token_when_none_is_cached(
        self, github_settings: Settings
    ) -> None:
        """The first call for an installation exchanges the app JWT for a token."""
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path.endswith("/access_tokens"):
                return json_response(
                    {
                        "token": "ghs_fresh",
                        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    },
                    status_code=201,
                )
            assert request.headers["authorization"] == "Bearer ghs_fresh"
            return json_response(
                {"id": 1, "name": "n", "full_name": "owner/name", "default_branch": "main"}
            )

        client = make_client(github_settings, handler, prime_token=False)
        client.get_repository(INSTALLATION_ID, "owner/name")

        assert paths == [
            f"/app/installations/{INSTALLATION_ID}/access_tokens",
            "/repos/owner/name",
        ]

    def test_reuses_a_cached_token_across_calls(self, github_settings: Settings) -> None:
        """Minting a token per request would double every call's latency."""
        token_requests = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal token_requests
            if request.url.path.endswith("/access_tokens"):
                token_requests += 1
                return json_response(
                    {
                        "token": "ghs_fresh",
                        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    },
                    status_code=201,
                )
            return json_response(
                {"id": 1, "name": "n", "full_name": "owner/name", "default_branch": "main"}
            )

        client = make_client(github_settings, handler, prime_token=False)
        client.get_repository(INSTALLATION_ID, "owner/name")
        client.get_repository(INSTALLATION_ID, "owner/other")

        assert token_requests == 1


class TestPagination:
    def test_follows_link_headers_to_collect_every_page(self, github_settings: Settings) -> None:
        """Reading only the first page would silently drop repositories from a
        large organisation -- a bug that appears only for the biggest users."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.params.get("page") == "2":
                return json_response(
                    {
                        "repositories": [
                            {
                                "id": 2,
                                "name": "b",
                                "full_name": "o/b",
                                "default_branch": "main",
                            }
                        ]
                    }
                )
            return httpx.Response(
                200,
                json={
                    "repositories": [
                        {"id": 1, "name": "a", "full_name": "o/a", "default_branch": "main"}
                    ]
                },
                headers={
                    "link": '<https://api.github.com/installation/repositories?page=2>; rel="next"'
                },
            )

        repositories = make_client(github_settings, handler).list_installation_repositories(
            INSTALLATION_ID
        )

        assert [r.full_name for r in repositories] == ["o/a", "o/b"]

    def test_requests_the_largest_page_size(self, github_settings: Settings) -> None:
        seen: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.params.get("per_page"))
            return json_response({"repositories": []})

        make_client(github_settings, handler).list_installation_repositories(INSTALLATION_ID)

        assert seen == ["100"]


class TestOAuth:
    """The OAuth exchange targets github.com, not the API host, so it does not
    go through the client's normal request plumbing."""

    @staticmethod
    def _route_httpx_post(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
        """Send module-level `httpx.post` calls to a mock transport."""
        stub = httpx.Client(transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
        monkeypatch.setattr(httpx, "post", stub.post)

    def test_exchanges_a_code_for_a_token(
        self, github_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "github.com"
            body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            assert body["code"] == "the-code"
            return json_response({"access_token": "gho_x", "token_type": "bearer"})

        self._route_httpx_post(monkeypatch, handler)

        token = make_client(github_settings, handler).exchange_oauth_code("the-code")

        assert token.access_token == "gho_x"

    def test_rejects_an_oauth_error_returned_with_status_200(
        self, github_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GitHub reports OAuth failures with HTTP 200 and an `error` key, so the
        status code alone is not enough to know the exchange worked."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=json.dumps(
                    {"error": "bad_verification_code", "error_description": "expired"}
                ),
                headers={"content-type": "application/json"},
            )

        self._route_httpx_post(monkeypatch, handler)

        with pytest.raises(GitHubAuthenticationError, match="expired"):
            make_client(github_settings, handler).exchange_oauth_code("stale")
