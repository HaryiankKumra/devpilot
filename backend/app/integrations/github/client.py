"""The GitHub API client.

`GitHubClient` is a Protocol rather than a base class so the mock implementation
does not inherit any real behaviour it might accidentally rely on. Everything
above this layer -- services, workers, routes -- depends only on the Protocol,
which is what lets the entire application run against the mock with no
credentials.

Error handling is the substance of this module. Every failure GitHub can
produce is mapped to one of the exceptions in `exceptions.py`, so callers make
one decision -- retry or not -- instead of inspecting status codes.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Protocol, Self, runtime_checkable

import httpx

from app.core.config import Settings
from app.core.logging import get_logger
from app.integrations.github.auth import InstallationTokenCache, build_app_jwt
from app.integrations.github.exceptions import (
    GitHubAuthenticationError,
    GitHubConfigurationError,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubResponseError,
    GitHubTransientError,
)
from app.integrations.github.models import (
    GitHubAccount,
    GitHubInstallation,
    GitHubInstallationToken,
    GitHubOAuthToken,
    GitHubPullRequest,
    GitHubRepository,
    PostedReview,
    ReviewComment,
)

logger = get_logger(__name__)

# Pinning the API version stops GitHub's own breaking changes from arriving
# unannounced; the header is how they let callers opt in deliberately.
GITHUB_API_VERSION = "2022-11-28"

ACCEPT_JSON = "application/vnd.github+json"
# The diff media type returns a unified diff as text rather than a JSON object.
ACCEPT_DIFF = "application/vnd.github.v3.diff"
# The raw media type returns file bytes directly instead of a base64 envelope.
ACCEPT_RAW = "application/vnd.github.raw"

# When the limit is hit with no usable reset header, wait this long.
DEFAULT_RATE_LIMIT_BACKOFF_SECONDS = 60


@runtime_checkable
class GitHubClient(Protocol):
    """What the rest of DevPilot needs from GitHub."""

    def list_installations(self) -> list[GitHubInstallation]: ...

    def list_installation_repositories(self, installation_id: int) -> list[GitHubRepository]: ...

    def get_repository(self, installation_id: int, full_name: str) -> GitHubRepository: ...

    def get_pull_request(
        self, installation_id: int, full_name: str, number: int
    ) -> GitHubPullRequest: ...

    def get_pull_request_diff(self, installation_id: int, full_name: str, number: int) -> str: ...

    def get_file_content(
        self, installation_id: int, full_name: str, path: str, ref: str
    ) -> str | None: ...

    def list_repository_files(
        self, installation_id: int, full_name: str, ref: str
    ) -> list[str]: ...

    def create_pull_request_review(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_sha: str,
        body: str,
        comments: list[ReviewComment],
    ) -> PostedReview: ...

    def exchange_oauth_code(self, code: str) -> GitHubOAuthToken: ...

    def get_authenticated_user(self, user_access_token: str) -> GitHubAccount: ...

    def close(self) -> None: ...


class RestGitHubClient:
    """Talks to the real GitHub REST API."""

    def __init__(
        self,
        settings: Settings,
        *,
        token_cache: InstallationTokenCache | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._token_cache = token_cache or InstallationTokenCache()
        self._client = httpx.Client(
            base_url=settings.github_api_url,
            timeout=settings.github_request_timeout_seconds,
            # A redirect from an API endpoint is not something we should follow
            # blindly with an Authorization header attached.
            follow_redirects=False,
            transport=transport,
        )

    # --- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    # --- request plumbing ----------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str,
        accept: str = ACCEPT_JSON,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Perform one request and translate failures into DevPilot exceptions."""
        try:
            response = self._client.request(
                method,
                path,
                params=params,
                json=json_body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": accept,
                    "X-GitHub-Api-Version": GITHUB_API_VERSION,
                    "User-Agent": "DevPilot",
                },
            )
        except httpx.TimeoutException as exc:
            raise GitHubTransientError(f"GitHub request timed out: {method} {path}") from exc
        except httpx.HTTPError as exc:
            raise GitHubTransientError(f"GitHub request failed: {exc}") from exc

        self._raise_for_status(response, method=method, path=path)
        return response

    def _raise_for_status(self, response: httpx.Response, *, method: str, path: str) -> None:
        if response.is_success:
            return

        status = response.status_code

        if status in (401, 403) and self._is_rate_limited(response):
            retry_after = self._retry_after_seconds(response)
            logger.warning("github.rate_limited", path=path, retry_after=retry_after)
            raise GitHubRateLimitError(
                f"GitHub rate limit exhausted for {method} {path}.",
                retry_after_seconds=retry_after,
            )

        if status == 401:
            raise GitHubAuthenticationError(f"GitHub rejected our credentials for {path}.")
        if status == 403:
            # Not rate limiting: the installation genuinely lacks permission.
            raise GitHubAuthenticationError(f"GitHub denied access to {path}.")
        if status == 404:
            raise GitHubNotFoundError(f"GitHub has no {path} visible to this installation.")
        if status == 429:
            raise GitHubRateLimitError(
                f"GitHub asked us to slow down on {path}.",
                retry_after_seconds=self._retry_after_seconds(response),
            )
        if status >= 500:
            raise GitHubTransientError(f"GitHub returned {status} for {method} {path}.")

        raise GitHubResponseError(f"GitHub returned {status} for {method} {path}.")

    @staticmethod
    def _is_rate_limited(response: httpx.Response) -> bool:
        """Distinguish a rate limit from a genuine permission failure.

        GitHub signals both with 403, so the remaining-requests header is what
        separates "come back later" from "you will never be allowed".
        """
        if response.headers.get("x-ratelimit-remaining") == "0":
            return True
        return "retry-after" in response.headers

    @staticmethod
    def _retry_after_seconds(response: httpx.Response) -> int:
        """How long GitHub wants us to wait, from whichever header it supplied."""
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return int(retry_after)

        reset_at = response.headers.get("x-ratelimit-reset")
        if reset_at and reset_at.isdigit():
            delta = int(reset_at) - int(datetime.now(UTC).timestamp())
            return max(1, delta)

        return DEFAULT_RATE_LIMIT_BACKOFF_SECONDS

    def _paginate(self, path: str, *, token: str, key: str | None = None) -> list[Any]:
        """Follow GitHub's `Link: rel="next"` headers and collect every page.

        Returning a partial first page would silently drop repositories from an
        organisation with more than 100 of them -- a bug that only appears for
        the largest, most important users.
        """
        items: list[Any] = []
        next_path: str | None = path
        params: dict[str, Any] | None = {"per_page": 100}

        while next_path is not None:
            response = self._request("GET", next_path, token=token, params=params)
            payload = response.json()
            page = payload.get(key, []) if key else payload
            if not isinstance(page, list):
                raise GitHubResponseError(f"Expected a list from {next_path}.")
            items.extend(page)

            next_link = response.links.get("next", {}).get("url")
            next_path = str(next_link) if next_link else None
            # The next URL already carries the query string.
            params = None

        return items

    # --- authentication ------------------------------------------------------

    def _installation_token(self, installation_id: int) -> str:
        cached = self._token_cache.get(installation_id)
        if cached is not None:
            return cached

        app_jwt = build_app_jwt(self._settings)
        response = self._request(
            "POST", f"/app/installations/{installation_id}/access_tokens", token=app_jwt
        )
        token = GitHubInstallationToken.model_validate(response.json())
        self._token_cache.store(installation_id, token)
        logger.info(
            "github.installation_token_issued",
            installation_id=installation_id,
            expires_at=token.expires_at.isoformat(),
        )
        return token.token

    # --- API surface ---------------------------------------------------------

    def list_installations(self) -> list[GitHubInstallation]:
        raw = self._paginate("/app/installations", token=build_app_jwt(self._settings))
        return [GitHubInstallation.model_validate(item) for item in raw]

    def list_installation_repositories(self, installation_id: int) -> list[GitHubRepository]:
        raw = self._paginate(
            "/installation/repositories",
            token=self._installation_token(installation_id),
            key="repositories",
        )
        return [GitHubRepository.model_validate(item) for item in raw]

    def get_repository(self, installation_id: int, full_name: str) -> GitHubRepository:
        response = self._request(
            "GET", f"/repos/{full_name}", token=self._installation_token(installation_id)
        )
        return GitHubRepository.model_validate(response.json())

    def get_pull_request(
        self, installation_id: int, full_name: str, number: int
    ) -> GitHubPullRequest:
        response = self._request(
            "GET",
            f"/repos/{full_name}/pulls/{number}",
            token=self._installation_token(installation_id),
        )
        return GitHubPullRequest.model_validate(response.json())

    def get_pull_request_diff(self, installation_id: int, full_name: str, number: int) -> str:
        """Return the pull request as a unified diff.

        The diff media type makes GitHub render the patch itself, which is far
        cheaper than listing files and fetching each one. GitHub computes it
        against the merge base, so it shows what the pull request changes rather
        than everything that has happened on the base branch since.
        """
        response = self._request(
            "GET",
            f"/repos/{full_name}/pulls/{number}",
            token=self._installation_token(installation_id),
            accept=ACCEPT_DIFF,
        )
        return response.text

    def get_file_content(
        self, installation_id: int, full_name: str, path: str, ref: str
    ) -> str | None:
        """Return a file's contents at `ref`, or `None` if it is not readable.

        Fetched with the raw media type so GitHub sends the bytes directly
        rather than a JSON envelope with base64 inside it.

        Returns `None` rather than raising for a missing file: a diff can name a
        path that no longer exists at the head commit (deleted later in the
        branch), and that is ordinary, not an error.
        """
        try:
            response = self._request(
                "GET",
                f"/repos/{full_name}/contents/{path}",
                token=self._installation_token(installation_id),
                accept=ACCEPT_RAW,
                params={"ref": ref},
            )
        except GitHubNotFoundError:
            logger.info("github.file_absent", full_name=full_name, path=path, ref=ref)
            return None

        try:
            return response.text
        except UnicodeDecodeError:
            # A binary file the diff did not flag. Nothing to analyse.
            logger.info("github.file_not_text", full_name=full_name, path=path)
            return None

    def list_repository_files(self, installation_id: int, full_name: str, ref: str) -> list[str]:
        """Return every file path in the repository at `ref`.

        One recursive tree call rather than walking directories: the latter
        would be hundreds of requests against the rate limit for a repository of
        any size. Only blobs are returned -- trees are directories, and commits
        are submodule pointers whose contents live in another repository we have
        no access to.
        """
        response = self._request(
            "GET",
            f"/repos/{full_name}/git/trees/{ref}",
            token=self._installation_token(installation_id),
            params={"recursive": "1"},
        )
        payload = response.json()

        if payload.get("truncated"):
            # GitHub caps the tree response. Indexing what came back is more
            # useful than refusing, but it must be visible in the logs.
            logger.warning("github.tree_truncated", full_name=full_name, ref=ref)

        tree = payload.get("tree", [])
        return [item["path"] for item in tree if item.get("type") == "blob" and "path" in item]

    def create_pull_request_review(
        self,
        installation_id: int,
        full_name: str,
        number: int,
        *,
        commit_sha: str,
        body: str,
        comments: list[ReviewComment],
    ) -> PostedReview:
        """Post one review carrying every inline comment.

        A single review rather than N standalone comments, for two reasons. It
        sends the author one notification instead of a dozen, and GitHub groups
        the comments under a single collapsible entry rather than scattering
        them through the timeline. The difference between a tool people keep
        installed and one they mute is largely this.

        `commit_sha` pins the review to the exact commit reviewed. Without it
        GitHub attaches comments to the branch head, which may already have
        moved -- putting the comment on a line the author has since rewritten.
        """
        payload: dict[str, Any] = {
            "commit_id": commit_sha,
            "body": body,
            # COMMENT rather than REQUEST_CHANGES: blocking a merge is a
            # decision for a human, not for an automated first pass.
            "event": "COMMENT",
            "comments": [comment.to_payload() for comment in comments],
        }

        response = self._request(
            "POST",
            f"/repos/{full_name}/pulls/{number}/reviews",
            token=self._installation_token(installation_id),
            json_body=payload,
        )
        data = response.json()
        return PostedReview(
            review_id=int(data["id"]),
            html_url=data.get("html_url"),
            comment_count=len(comments),
        )

    # --- OAuth (user identity, not installation access) ----------------------

    def exchange_oauth_code(self, code: str) -> GitHubOAuthToken:
        """Trade an OAuth callback code for a user access token.

        This uses github.com rather than api.github.com and is unauthenticated
        apart from the client secret, so it does not go through `_request`.
        """
        settings = self._settings
        if not settings.github_client_id or not settings.github_client_secret:
            raise GitHubConfigurationError(
                "GitHub OAuth is not configured. Set DEVPILOT_GITHUB_CLIENT_ID and "
                "DEVPILOT_GITHUB_CLIENT_SECRET."
            )

        try:
            response = httpx.post(
                f"{settings.github_web_url}/login/oauth/access_token",
                data={
                    "client_id": settings.github_client_id,
                    "client_secret": settings.github_client_secret.get_secret_value(),
                    "code": code,
                    "redirect_uri": settings.github_oauth_redirect_uri,
                },
                headers={"Accept": ACCEPT_JSON},
                timeout=settings.github_request_timeout_seconds,
            )
        except httpx.HTTPError as exc:
            raise GitHubTransientError(
                f"Could not reach GitHub to exchange the code: {exc}"
            ) from exc

        if not response.is_success:
            raise GitHubResponseError(
                f"GitHub returned {response.status_code} exchanging the OAuth code."
            )

        payload = response.json()
        # GitHub reports OAuth failures with HTTP 200 and an `error` key, so the
        # status code alone is not enough to know this worked.
        if "error" in payload:
            detail = payload.get("error_description", payload["error"])
            raise GitHubAuthenticationError(f"GitHub rejected the OAuth code: {detail}")
        return GitHubOAuthToken.model_validate(payload)

    def get_authenticated_user(self, user_access_token: str) -> GitHubAccount:
        response = self._request("GET", "/user", token=user_access_token)
        return GitHubAccount.model_validate(response.json())
