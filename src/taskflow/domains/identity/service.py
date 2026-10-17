import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings
from taskflow.core.exceptions import DomainException
from taskflow.core.security import hash_password, verify_password
from taskflow.domains.identity.models import Session, User
from taskflow.domains.identity.repository import SessionRepository, UserRepository
from taskflow.domains.identity.schemas import LoginRequest, RegisterRequest


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
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[User, str, str]:
        """Register a user, store hashed credentials, and issue an active session."""
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

        # Create authentication session
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
