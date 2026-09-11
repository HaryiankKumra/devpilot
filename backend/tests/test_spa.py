"""Tests for serving the built frontend from the API process.

Two things are easy to get wrong and both would be invisible in a unit test of
the API alone: the API's strict CSP blanking the HTML page, and the SPA
fallback turning every unknown API path into a 200 with a web page in it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.spa import FRONTEND_CONTENT_SECURITY_POLICY
from app.core.config import Environment, Settings
from app.main import create_app


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """A minimal built bundle: index.html, one hashed asset, one root file."""
    (tmp_path / "index.html").write_text("<!doctype html><title>DevPilot</title>", "utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "index-abc123.js").write_text("console.log(1)", "utf-8")
    (tmp_path / "favicon.svg").write_text("<svg/>", "utf-8")
    return tmp_path


@pytest.fixture
def client(static_dir: Path) -> TestClient:
    app = create_app(
        Settings(
            _env_file=None,
            environment=Environment.CI,
            rate_limit_enabled=False,
            static_dir=static_dir,
        )
    )
    return TestClient(app)


class TestDocument:
    def test_root_serves_index(self, client: TestClient) -> None:
        response = client.get("/")

        assert response.status_code == 200
        assert "<title>DevPilot</title>" in response.text

    def test_client_routes_fall_back_to_index(self, client: TestClient) -> None:
        """A refresh on /reviews/42 must load the app, not 404."""
        response = client.get("/reviews/42")

        assert response.status_code == 200
        assert "<title>DevPilot</title>" in response.text

    def test_document_gets_the_frontend_csp_not_the_api_one(self, client: TestClient) -> None:
        """The API middleware sets default-src 'none' on everything, which would
        stop the page loading its own scripts. The document must override it."""
        response = client.get("/")

        assert response.headers["Content-Security-Policy"] == FRONTEND_CONTENT_SECURITY_POLICY
        assert "script-src 'self'" in response.headers["Content-Security-Policy"]

    def test_document_is_never_cached(self, client: TestClient) -> None:
        """Or clients keep a document referencing asset hashes the last deploy deleted."""
        assert client.get("/").headers["Cache-Control"] == "no-cache"

    def test_other_hardening_headers_still_apply(self, client: TestClient) -> None:
        response = client.get("/")

        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["X-Content-Type-Options"] == "nosniff"


class TestAssets:
    def test_hashed_assets_are_served(self, client: TestClient) -> None:
        response = client.get("/assets/index-abc123.js")

        assert response.status_code == 200
        assert response.text == "console.log(1)"

    def test_root_files_are_served_as_themselves(self, client: TestClient) -> None:
        response = client.get("/favicon.svg")

        assert response.status_code == 200
        assert response.text == "<svg/>"


class TestApiPathsAreNotSwallowed:
    """The catch-all must lose to every real route and refuse API-shaped paths."""

    def test_health_still_works(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_real_api_routes_still_work(self, client: TestClient) -> None:
        # 401, not 200-with-HTML: the API route matched, not the fallback.
        assert client.get("/api/v1/auth/me").status_code == 401

    @pytest.mark.parametrize("path", ["/api/v1/nope", "/api/anything", "/healthz", "/docs/nope"])
    def test_unknown_api_paths_are_404_not_index(self, client: TestClient, path: str) -> None:
        response = client.get(path)

        assert response.status_code == 404
        assert "<title>" not in response.text


class TestConfiguration:
    def test_not_mounted_when_unset(self) -> None:
        """Compose serves the bundle from nginx; the API must not pretend to."""
        app = create_app(
            Settings(_env_file=None, environment=Environment.CI, rate_limit_enabled=False)
        )

        assert TestClient(app).get("/reviews/42").status_code == 404

    def test_refuses_a_directory_without_index(self, tmp_path: Path) -> None:
        """Loud at startup, not a mysterious 404 on every page."""
        with pytest.raises(RuntimeError, match=r"index.html"):
            create_app(
                Settings(
                    _env_file=None,
                    environment=Environment.CI,
                    rate_limit_enabled=False,
                    static_dir=tmp_path,
                )
            )
