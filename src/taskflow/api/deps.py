from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, Request, WebSocket, status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings
from taskflow.core.exceptions import DomainException
from taskflow.db.session import get_db_session
from taskflow.domains.identity.models import Session, User
from taskflow.domains.identity.service import AuthService

if TYPE_CHECKING:
    from taskflow.domains.collaboration.service import CollaborationService
    from taskflow.domains.organizations.service import OrganizationService
    from taskflow.domains.projects.service import ProjectService
    from taskflow.domains.reports.service import ReportService
    from taskflow.domains.scrum.service import ScrumService
    from taskflow.domains.work_items.service import (
        DashboardService,
        SavedFilterService,
        TaskService,
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for obtaining an asynchronous database session."""
    async for session in get_db_session():
        yield session


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthService:
    """Dependency injecting AuthService with active DB session."""
    return AuthService(session)


def get_organization_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> "OrganizationService":
    """Dependency injecting OrganizationService with active DB session."""
    from taskflow.domains.organizations.service import OrganizationService

    return OrganizationService(session)


def get_dashboard_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> "DashboardService":
    """Dependency injecting DashboardService with active DB session."""
    from taskflow.domains.work_items.service import DashboardService

    return DashboardService(session)


def get_project_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> "ProjectService":
    """Dependency injecting ProjectService with active DB session."""
    from taskflow.domains.projects.service import ProjectService

    return ProjectService(session)


def get_task_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> "TaskService":
    """Dependency injecting TaskService with active DB session."""
    from taskflow.domains.work_items.service import TaskService

    return TaskService(session)


def get_collaboration_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> "CollaborationService":
    """Dependency injecting CollaborationService with active DB session."""
    from taskflow.domains.collaboration.service import CollaborationService

    return CollaborationService(session)


def get_saved_filter_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> "SavedFilterService":
    """Dependency injecting SavedFilterService with active DB session."""
    from taskflow.domains.work_items.service import SavedFilterService

    return SavedFilterService(session)


def get_scrum_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> "ScrumService":
    """Dependency injecting ScrumService with active DB session."""
    from taskflow.domains.scrum.service import ScrumService

    return ScrumService(session)


def get_report_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> "ReportService":
    """Dependency injecting ReportService with active DB session."""
    from taskflow.domains.reports.service import ReportService

    return ReportService(session)


class SessionCredentials:
    def __init__(
        self,
        user: User,
        session: Session,
        raw_token: str,
        token_source: str,
    ) -> None:
        self.user = user
        self.session = session
        self.raw_token = raw_token
        self.token_source = token_source


async def get_current_session_credentials(
    request: Request,
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> SessionCredentials:
    """
    Extract session token from HttpOnly cookie or Authorization Bearer header,
    validate against database, and enforce CSRF protection on state-changing requests.
    """
    raw_token: str | None = None
    token_source: str = "none"

    # 1. Inspect Authorization Bearer header first
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        raw_token = auth_header[7:].strip()
        token_source = "bearer"

    # 2. Inspect session cookie if Bearer header is not present
    if not raw_token:
        cookie_token = request.cookies.get(settings.SESSION_COOKIE_NAME)
        if cookie_token:
            raw_token = cookie_token.strip()
            token_source = "cookie"

    if not raw_token:
        raise DomainException(
            code="UNAUTHORIZED",
            message="Authentication credentials were not provided.",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    # Validate session and retrieve user
    user, db_session = await auth_service.validate_session(raw_token)

    # 3. Enforce CSRF protection for cookie-authenticated state-changing requests
    if token_source == "cookie" and request.method in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }:
        if not x_csrf_token or x_csrf_token != db_session.csrf_token:
            raise DomainException(
                code="CSRF_TOKEN_INVALID",
                message="Invalid or missing CSRF token in X-CSRF-Token header.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

    return SessionCredentials(
        user=user,
        session=db_session,
        raw_token=raw_token,
        token_source=token_source,
    )


async def get_current_user(
    credentials: Annotated[
        SessionCredentials, Depends(get_current_session_credentials)
    ],
) -> User:
    """Dependency injecting the authenticated User entity."""
    return credentials.user


async def get_current_active_user(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Dependency verifying that the authenticated user account is active."""
    if not user.is_active:
        raise DomainException(
            code="USER_INACTIVE",
            message="Account is inactive.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return user


async def require_superuser(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Dependency ensuring that the current active user has administrator privileges."""
    if not current_user.is_superuser:
        raise DomainException(
            code="FORBIDDEN",
            message="Administrator privileges required.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return current_user


async def authenticate_websocket(
    websocket: WebSocket,
    session: AsyncSession,
) -> User | None:
    """
    Authenticate a WebSocket connection using the session cookie or
    Authorization header.
    Closes the connection with WS_1008_POLICY_VIOLATION if credentials are
    missing, invalid, expired, or belong to an inactive user.
    """
    raw_token: str | None = None
    token_source: str = "none"
    auth_header = websocket.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        raw_token = auth_header[7:].strip()
        token_source = "bearer"

    if not raw_token:
        cookie_token = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
        if cookie_token:
            raw_token = cookie_token.strip()
            token_source = "cookie"

    if not raw_token:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Authentication credentials were not provided.",
        )
        return None

    # Enforce Origin validation for cookie-based authentication to mitigate CSWSH
    if token_source == "cookie":
        origin = websocket.headers.get("origin")
        if origin is not None:
            allowed_origins = set(settings.BACKEND_CORS_ORIGINS) | {
                "http://testserver",
                "https://testserver",
                "http://localhost",
                "http://127.0.0.1",
            }
            origin_clean = origin.rstrip("/")
            is_allowed = (
                "*" in settings.BACKEND_CORS_ORIGINS
                or origin_clean in allowed_origins
                or any(origin_clean == o.rstrip("/") for o in allowed_origins)
            )
            if not is_allowed:
                await websocket.close(
                    code=status.WS_1008_POLICY_VIOLATION,
                    reason="Origin not allowed.",
                )
                return None

    auth_service = AuthService(session)
    try:
        user, _ = await auth_service.validate_session(raw_token)
    except DomainException:
        await websocket.close(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid, expired, or inactive session.",
        )
        return None

    return user


