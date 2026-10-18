import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings
from taskflow.core.exceptions import DomainException
from taskflow.core.security import hash_password, verify_password
from taskflow.domains.identity.models import Session, User
from taskflow.domains.identity.repository import SessionRepository, UserRepository
from taskflow.domains.identity.schemas import (
    LoginRequest,
    RegisterRequest,
    UserUpdateAdminRequest,
)


def hash_token(raw_token: str) -> str:
    """Generate SHA-256 hash digest of a plaintext token."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


class AuthService:
    """IAM service coordinating transactions and business rules."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.user_repo = UserRepository(session)
        self.session_repo = SessionRepository(session)

    async def register(
        self,
        request: RegisterRequest,
    ) -> User:
        """Register a user and store hashed credentials without issuing a session."""
        if not settings.ENABLE_PUBLIC_REGISTRATION:
            raise DomainException(
                code="REGISTRATION_DISABLED",
                message="Public registration is currently disabled.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        existing_user = await self.user_repo.get_by_email(request.email)
        if existing_user is not None:
            raise DomainException(
                code="EMAIL_ALREADY_EXISTS",
                message="A user with this email address already exists.",
                status_code=status.HTTP_409_CONFLICT,
                details={"email": request.email},
            )

        # Hash password using Argon2id
        pwd_hash = hash_password(request.password)

        # Create user and credentials
        user = await self.user_repo.create_user(
            email=request.email,
            full_name=request.full_name,
        )
        await self.user_repo.create_credentials(
            user_id=user.id,
            password_hash=pwd_hash,
        )

        # Service-level transaction boundary commit
        await self.session.commit()
        await self.session.refresh(user)

        return user

    async def login(
        self,
        request: LoginRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str, str]:
        """Authenticate user by email and password, issuing an active session."""
        user = await self.user_repo.get_by_email(request.email)
        if user is None or user.credentials is None:
            raise DomainException(
                code="INVALID_CREDENTIALS",
                message="Invalid email or password.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if not verify_password(request.password, user.credentials.password_hash):
            raise DomainException(
                code="INVALID_CREDENTIALS",
                message="Invalid email or password.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_active:
            raise DomainException(
                code="USER_INACTIVE",
                message="Your account is inactive. Please contact support.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        raw_token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_hex(32)
        token_digest = hash_token(raw_token)
        expires_at = datetime.now(UTC) + timedelta(
            minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES
        )

        await self.session_repo.create_session(
            user_id=user.id,
            token_hash=token_digest,
            csrf_token=csrf_token,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )

        # Service-level transaction boundary commit
        await self.session.commit()
        await self.session.refresh(user)

        return user, raw_token, csrf_token

    async def logout(self, raw_token: str) -> None:
        """Invalidate the session corresponding to the provided token."""
        token_digest = hash_token(raw_token)
        await self.session_repo.delete_by_token_hash(token_digest)
        await self.session.commit()

    async def validate_session(self, raw_token: str) -> tuple[User, Session]:
        """Validate token against active non-expired sessions."""
        token_digest = hash_token(raw_token)
        db_session = await self.session_repo.get_by_token_hash(token_digest)

        if db_session is None:
            raise DomainException(
                code="UNAUTHORIZED",
                message="Session is invalid or does not exist.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        expires_at = db_session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        if expires_at <= datetime.now(UTC):
            # Session expired, delete lazily
            await self.session_repo.delete_by_token_hash(token_digest)
            await self.session.commit()
            raise DomainException(
                code="UNAUTHORIZED",
                message="Session has expired. Please log in again.",
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        if not db_session.user.is_active:
            raise DomainException(
                code="USER_INACTIVE",
                message="Your account is inactive. Please contact support.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        return db_session.user, db_session

    async def list_users(
        self,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        is_active: bool | None = None,
        is_superuser: bool | None = None,
    ) -> tuple[list[User], int]:
        """List users with pagination and filters."""
        skip = (page - 1) * page_size
        users = await self.user_repo.list_users(
            skip=skip,
            limit=page_size,
            search=search,
            is_active=is_active,
            is_superuser=is_superuser,
        )
        total = await self.user_repo.count_users(
            search=search,
            is_active=is_active,
            is_superuser=is_superuser,
        )
        return users, total

    async def get_user_by_id(self, user_id: uuid.UUID) -> User:
        """Fetch user by ID or raise USER_NOT_FOUND."""
        user = await self.user_repo.get_by_id(user_id)
        if user is None:
            raise DomainException(
                code="USER_NOT_FOUND",
                message="User not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"user_id": str(user_id)},
            )
        return user

    async def update_user_by_admin(
        self,
        user_id: uuid.UUID,
        request: "UserUpdateAdminRequest",
        current_admin_id: uuid.UUID,
    ) -> User:
        """Admin updating user details, activation status, or admin privileges."""
        user = await self.get_user_by_id(user_id)

        # Prevent admin from deactivating or revoking superuser privileges from self
        if user.id == current_admin_id:
            if request.is_active is False:
                raise DomainException(
                    code="CANNOT_DEACTIVATE_SELF",
                    message="Administrators cannot deactivate their own account.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if request.is_superuser is False:
                raise DomainException(
                    code="CANNOT_DEMOTE_SELF",
                    message=(
                        "Administrators cannot revoke their own superuser privileges."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        # Update user
        updated_user = await self.user_repo.update_user(
            user=user,
            full_name=request.full_name,
            is_active=request.is_active,
            is_superuser=request.is_superuser,
        )

        # If deactivated, revoke all active sessions to immediately terminate access
        if request.is_active is False:
            await self.session_repo.delete_all_user_sessions(user.id)

        await self.session.commit()
        await self.session.refresh(updated_user)
        return updated_user
