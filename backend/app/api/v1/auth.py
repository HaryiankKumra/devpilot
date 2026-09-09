"""Registration, login and current-user endpoints.

These routes are thin on purpose: validate input via a schema, call one service
function, map the outcome to a status code. Anything that decides *what
happened* lives in `app.services.auth`.
"""

from __future__ import annotations

from fastapi import APIRouter, status

from app.api.deps import AppSettings, CurrentUser, DbSession
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserRead
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
    responses={409: {"description": "The email address is already registered."}},
)
def register(payload: RegisterRequest, session: DbSession) -> UserRead:
    """Create a new account and return it.

    Deliberately does *not* return a token: registration and login are separate
    steps, so an account created by an administrator or a future invite flow
    does not implicitly hand out a session.
    """
    user = auth_service.register_user(
        session,
        email=payload.email,
        password=payload.password,
        full_name=payload.full_name,
    )
    # The service layer leaves transaction boundaries to its caller, so the
    # route commits once the unit of work is complete.
    session.commit()
    return UserRead.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for an access token",
    responses={
        401: {"description": "Incorrect email or password."},
        403: {"description": "The account is disabled."},
    },
)
def login(payload: LoginRequest, session: DbSession, settings: AppSettings) -> TokenResponse:
    """Authenticate and return a bearer token."""
    user = auth_service.authenticate_user(session, email=payload.email, password=payload.password)
    token, expires_in = auth_service.issue_access_token(user, settings)
    # Authentication can rewrite the stored digest when cost parameters have
    # been raised, so this commit is not redundant.
    session.commit()
    return TokenResponse(access_token=token, expires_in=expires_in)


@router.get(
    "/me",
    response_model=UserRead,
    summary="The authenticated user",
    responses={401: {"description": "Missing, invalid or expired token."}},
)
def read_current_user(user: CurrentUser) -> UserRead:
    """Return the account belonging to the supplied token."""
    return UserRead.model_validate(user)
