"""Chooses between the real GitHub client and the mock.

Isolated in its own module so the decision is made in exactly one place. Nothing
else in the codebase asks whether GitHub is mocked; callers receive a
`GitHubClient` and cannot tell the difference.
"""

from __future__ import annotations

from app.core.config import Settings
from app.integrations.github.auth import InstallationTokenCache
from app.integrations.github.client import GitHubClient, RestGitHubClient
from app.integrations.github.mock import MockGitHubClient

# Installation tokens are valid for an hour and cost a round trip to mint, so
# the cache is shared across clients for the lifetime of the process.
_token_cache = InstallationTokenCache()


def build_github_client(settings: Settings) -> GitHubClient:
    """Return a client appropriate to the configured mode."""
    if settings.github_is_mocked:
        return MockGitHubClient()
    return RestGitHubClient(settings, token_cache=_token_cache)


def reset_token_cache() -> None:
    """Discard cached installation tokens. Used by tests."""
    _token_cache.clear()
