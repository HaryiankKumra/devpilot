"""Failures specific to talking to GitHub.

These are distinct from `DevPilotError` because most of them are not the
caller's fault and not directly translatable to an HTTP status: a rate limit or
a 502 from GitHub means "retry later", which is a worker concern, not something
to hand a browser.
"""

from __future__ import annotations


class GitHubError(Exception):
    """Base class for GitHub integration failures."""


class GitHubConfigurationError(GitHubError):
    """DevPilot is not configured to talk to GitHub.

    Raised at the point of use rather than at startup, so the rest of the
    application still runs (and the mock mode still works) when no GitHub App
    has been registered yet.
    """


class GitHubAuthenticationError(GitHubError):
    """GitHub rejected our credentials.

    Usually a wrong app id, a private key that does not belong to the app, or a
    revoked installation. Retrying does not help.
    """


class GitHubNotFoundError(GitHubError):
    """The resource does not exist, or the installation cannot see it.

    GitHub deliberately answers 404 rather than 403 for private resources the
    caller may not know about, so these two cases are indistinguishable.
    """


class GitHubRateLimitError(GitHubError):
    """The rate limit is exhausted.

    Carries the number of seconds to wait, taken from GitHub's own headers, so
    a caller can back off for exactly as long as required instead of guessing.
    """

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class GitHubTransientError(GitHubError):
    """A failure that may well succeed on retry: 5xx, timeout, connection reset."""


class GitHubResponseError(GitHubError):
    """GitHub answered with something we could not interpret.

    Treated as a hard failure rather than a transient one: retrying an
    unparseable response usually just produces another unparseable response.
    """
