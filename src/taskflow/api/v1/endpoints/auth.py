from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status

from taskflow.api.deps import (
    SessionCredentials,
    get_auth_service,
    get_current_active_user,
    get_current_session_credentials,
)
from taskflow.core.config import settings
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import (
    AuthResponse,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    UserResponse,
)
from taskflow.domains.identity.service import AuthService

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _set_auth_cookies(
    response: Response,
    session_token: str,
    csrf_token: str,
) -> None:
    """Set secure HttpOnly session cookie and accessible CSRF cookie."""
    max_age = settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60

    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_token,
        max_age=max_age,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )
    response.set_cookie(
        key=settings.CSRF_COOKIE_NAME,
        value=csrf_token,
        max_age=max_age,
        httponly=False,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    """Delete session and CSRF cookies."""
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
    )
    response.delete_cookie(
        key=settings.CSRF_COOKIE_NAME,
        path="/",
    )


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register new user account",
    description=(
        "Registers a new user, hashes password with Argon2id, issues a new session, "
        "and sets HttpOnly session and CSRF cookies."
    ),
)
async def register(
    request: Request,
    response: Response,
    payload: RegisterRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    """Handle new user registration and issue authenticated session."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    user, session_token, csrf_token = await auth_service.register(
        request=payload,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    _set_auth_cookies(response, session_token, csrf_token)

    return AuthResponse(
        user=UserResponse.model_validate(user),
        csrf_token=csrf_token,
        message="User registered successfully",
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    summary="User login",
    description=(
        "Authenticates a user via email and password, creates an active session, "
        "and sets HttpOnly session and CSRF cookies."
    ),
)
async def login(
    request: Request,
    response: Response,
    payload: LoginRequest,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> AuthResponse:
    """Authenticate user and issue active session."""
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("User-Agent")

    user, session_token, csrf_token = await auth_service.login(
        request=payload,
        ip_address=client_ip,
        user_agent=user_agent,
    )

    _set_auth_cookies(response, session_token, csrf_token)

    return AuthResponse(
        user=UserResponse.model_validate(user),
        csrf_token=csrf_token,
        message="Authenticated successfully",
    )


@router.post(
    "/logout",
    response_model=MessageResponse,
    status_code=status.HTTP_200_OK,
    summary="User logout",
    description=(
        "Revokes the current session from the database and clears "
        "authentication cookies. When using cookie authentication, "
        "X-CSRF-Token header matching session CSRF token is required."
    ),
)
async def logout(
    response: Response,
    credentials: Annotated[
        SessionCredentials, Depends(get_current_session_credentials)
    ],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> MessageResponse:
    """Revoke session and clear cookies."""
    await auth_service.logout(credentials.raw_token)
    _clear_auth_cookies(response)
    return MessageResponse(message="Successfully logged out")


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Get current user profile",
    description=(
        "Returns profile of currently authenticated user based on session "
        "cookie or Authorization Bearer token."
    ),
)
async def get_current_user_profile(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> UserResponse:
    """Return profile of currently authenticated user."""
    return UserResponse.model_validate(current_user)
