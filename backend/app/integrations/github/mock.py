"""An in-process fake GitHub, used when `DEVPILOT_GITHUB_MODE=mock`.

Registering a GitHub App, generating a private key and exposing a public
webhook URL is a real barrier to running this project. The mock removes it: the
whole application -- repository sync, webhook handling, the dashboard -- works
end to end with no credentials at all.

Two rules keep this honest rather than a lie that hides bugs:

* It returns the same **types** the real client returns, validated through the
  same Pydantic models. Code that works here works against the real API.
* It never pretends a review happened. It supplies repositories and pull
  requests; everything downstream is the genuine pipeline.

The data below is fixed rather than random so that tests and screenshots are
reproducible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.logging import get_logger
from app.integrations.github.exceptions import GitHubNotFoundError
from app.integrations.github.models import (
    GitHubAccount,
    GitHubInstallation,
    GitHubOAuthToken,
    GitHubPullRequest,
    GitHubPullRequestRef,
    GitHubRepository,
)

logger = get_logger(__name__)

MOCK_INSTALLATION_ID = 10_000_001
MOCK_USER_ACCESS_TOKEN = "mock-user-access-token"

MOCK_ACCOUNT = GitHubAccount(
    id=4_242_001, login="devpilot-demo", type="Organization", avatar_url=None
)

MOCK_REPOSITORIES: tuple[GitHubRepository, ...] = (
    GitHubRepository(
        id=900_000_001,
        name="checkout-service",
        full_name="devpilot-demo/checkout-service",
        private=True,
        default_branch="main",
        owner=MOCK_ACCOUNT,
        html_url="https://github.com/devpilot-demo/checkout-service",
    ),
    GitHubRepository(
        id=900_000_002,
        name="billing-api",
        full_name="devpilot-demo/billing-api",
        private=False,
        default_branch="main",
        owner=MOCK_ACCOUNT,
        html_url="https://github.com/devpilot-demo/billing-api",
    ),
)

# The pull request model mirrors GitHub's, which does not name its own
# repository, so the association is kept alongside it.
MOCK_PULL_REQUEST_REPOSITORY = "devpilot-demo/checkout-service"

MOCK_PULL_REQUEST = GitHubPullRequest(
    id=800_000_001,
    number=42,
    title="Add coupon validation to checkout",
    state="open",
    draft=False,
    merged=False,
    user=GitHubAccount(id=4_242_009, login="demo-developer", type="User"),
    head=GitHubPullRequestRef(ref="feature/coupon-validation", sha="a" * 40),
    base=GitHubPullRequestRef(ref="main", sha="b" * 40),
    html_url="https://github.com/devpilot-demo/checkout-service/pull/42",
)


class MockGitHubClient:
    """Serves canned data with the same shape as the real client."""

    def __init__(self) -> None:
        logger.info("github.mock_mode_enabled")

    def list_installations(self) -> list[GitHubInstallation]:
        return [
            GitHubInstallation(
                id=MOCK_INSTALLATION_ID,
                account=MOCK_ACCOUNT,
                repository_selection="all",
            )
        ]

    def list_installation_repositories(self, installation_id: int) -> list[GitHubRepository]:
        if installation_id != MOCK_INSTALLATION_ID:
            raise GitHubNotFoundError(f"Mock GitHub has no installation {installation_id}.")
        return list(MOCK_REPOSITORIES)

    def get_repository(self, installation_id: int, full_name: str) -> GitHubRepository:
        for repository in MOCK_REPOSITORIES:
            if repository.full_name == full_name:
                return repository
        raise GitHubNotFoundError(f"Mock GitHub has no repository {full_name}.")

    def get_pull_request(
        self, installation_id: int, full_name: str, number: int
    ) -> GitHubPullRequest:
        if full_name != MOCK_PULL_REQUEST_REPOSITORY or number != MOCK_PULL_REQUEST.number:
            raise GitHubNotFoundError(f"Mock GitHub has no {full_name}#{number}.")
        return MOCK_PULL_REQUEST

    def get_pull_request_diff(self, installation_id: int, full_name: str, number: int) -> str:
        """A small but genuine unified diff, including a deliberate flaw.

        The added code has a real problem a linter will catch (an unused import
        and a bare `except`), so the static-analysis stage has something honest
        to find rather than always returning an empty list.
        """
        if full_name != MOCK_PULL_REQUEST_REPOSITORY or number != MOCK_PULL_REQUEST.number:
            raise GitHubNotFoundError(f"Mock GitHub has no {full_name}#{number}.")
        return MOCK_DIFF

    def get_file_content(
        self, installation_id: int, full_name: str, path: str, ref: str
    ) -> str | None:
        return MOCK_FILE_CONTENTS.get(path)

    def list_repository_files(self, installation_id: int, full_name: str, ref: str) -> list[str]:
        return sorted(MOCK_FILE_CONTENTS)

    def exchange_oauth_code(self, code: str) -> GitHubOAuthToken:
        return GitHubOAuthToken(
            access_token=MOCK_USER_ACCESS_TOKEN, token_type="bearer", scope="read:user"
        )

    def get_authenticated_user(self, user_access_token: str) -> GitHubAccount:
        return GitHubAccount(id=4_242_100, login="demo-developer", type="User")

    def close(self) -> None:
        """Nothing to release; present so the Protocol is satisfied."""


MOCK_DIFF = """diff --git a/checkout/coupons.py b/checkout/coupons.py
index 1111111..2222222 100644
--- a/checkout/coupons.py
+++ b/checkout/coupons.py
@@ -1,6 +1,14 @@
 import json
+import os
 
 
 def apply_coupon(order, code):
-    return order
+    try:
+        discount = LOOKUP[code]
+    except:
+        discount = 0
+    order["total"] = order["total"] - discount
+    return order
"""

# Keyed by path, as `get_file_content` receives it. This is the file as it
# stands *after* the diff, which is what a linter should be run against.
MOCK_COUPONS_FILE = """import json
import os


def apply_coupon(order, code):
    try:
        discount = LOOKUP[code]
    except:
        discount = 0
    order["total"] = order["total"] - discount
    return order
"""

# A second file, so retrieval has something to choose *between*. It defines the
# LOOKUP table the changed code refers to, which is exactly the kind of context
# retrieval is supposed to surface.
MOCK_DISCOUNTS_FILE = '''"""Coupon definitions used across checkout."""

LOOKUP = {
    "WELCOME10": 10,
    "SUMMER20": 20,
}


def is_valid_coupon(code):
    """Return True when the coupon exists and has not expired."""
    return code in LOOKUP
'''

MOCK_README = """# Checkout service

Applies coupons to orders. Coupon codes are defined in `checkout/discounts.py`;
never hardcode a discount amount at the call site.
"""

MOCK_FILE_CONTENTS: dict[str, str] = {
    "checkout/coupons.py": MOCK_COUPONS_FILE,
    "checkout/discounts.py": MOCK_DISCOUNTS_FILE,
    "README.md": MOCK_README,
}


def mock_installation_expiry() -> datetime:
    """An expiry far enough out that nothing in a test session refreshes."""
    return datetime.now(UTC) + timedelta(hours=1)
