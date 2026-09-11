"""GitHub account linking (OAuth).

The callback is the one endpoint in DevPilot that a browser reaches while *not*
carrying an Authorization header -- GitHub redirects the user here directly. The
signed `state` parameter is therefore what identifies the user, and validating
it is what stops an attacker attaching their GitHub identity to someone else's
account.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from fastapi.responses import RedirectResponse

from app.api.deps import AppSettings, CurrentUser, DbSession, GitHub
from app.core.exceptions import NotAuthenticatedError
from app.core.logging import get_logger
from app.db.repositories.user import UserRepository
from app.integrations.github.exceptions import GitHubConfigurationError
from app.schemas.auth import UserRead
from app.schemas.repository import GitHubAuthorizeStart, GitHubLinkStatus
from app.services import github_link

logger = get_logger(__name__)

router = APIRouter(prefix="/github", tags=["github"])


def _install_url(settings: AppSettings) -> str | None:
    try:
        return github_link.build_installation_url(settings)
    except GitHubConfigurationError:
        # No app slug configured: the UI hides the button rather than offering
        # a link that leads nowhere.
        return None


@router.get("/status", response_model=GitHubLinkStatus, summary="GitHub link status")
def link_status(user: CurrentUser, settings: AppSettings) -> GitHubLinkStatus:
    return GitHubLinkStatus(
        linked=user.has_linked_github,
        github_login=user.github_login,
        install_url=_install_url(settings),
    )


@router.get(
    "/authorize",
    response_model=GitHubAuthorizeStart,
    summary="Begin linking a GitHub account",
)
def authorize(user: CurrentUser, settings: AppSettings) -> GitHubAuthorizeStart:
    """Return the URL that starts the OAuth flow, carrying a signed state.

    JSON rather than a redirect: this route needs the Bearer token to know
    *which* user is linking, and a browser navigation cannot send one. The
    frontend calls this with the token, then navigates to the URL it gets back.
    The signed `state` inside it is what ties the eventual callback to this
    user, so nothing is lost by the extra hop.
    """
    return GitHubAuthorizeStart(authorize_url=github_link.build_authorize_url(user, settings))


@router.get(
    "/callback",
    status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    summary="GitHub OAuth callback",
    response_class=RedirectResponse,
    responses={400: {"description": "Missing or invalid state."}},
)
def callback(
    code: str,
    state: str,
    session: DbSession,
    settings: AppSettings,
    client: GitHub,
) -> RedirectResponse:
    """Complete the link and send the browser back to the frontend.

    Not protected by `CurrentUser`: GitHub redirects the browser here, and that
    request carries no Authorization header. The signed `state` is what proves
    which user began the flow.
    """
    user_id = github_link.decode_state(state, settings)

    user = UserRepository(session).get_by_id(user_id)
    if user is None or not user.is_active:
        # The account was deleted or disabled between starting and finishing.
        raise NotAuthenticatedError()

    github_link.complete_link(session, user=user, code=code, client=client)
    session.commit()

    # Redirect rather than render: the user is in a browser mid-flow, and the
    # frontend owns what they should see next.
    return RedirectResponse(
        f"{settings.frontend_base_url}/settings?github=linked",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
    )


@router.delete(
    "/link",
    response_model=UserRead,
    summary="Unlink the GitHub account",
)
def unlink(user: CurrentUser, session: DbSession) -> UserRead:
    """Detach the GitHub identity, leaving the DevPilot account and its history."""
    github_link.unlink(user)
    session.commit()
    return UserRead.model_validate(user)
