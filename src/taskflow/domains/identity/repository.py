import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.identity.models import Session, User, UserCredentials


class UserRepository:
    """Persistence operations for User and UserCredentials entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        """Fetch user by primary key."""
        stmt = (
            select(User)
            .options(selectinload(User.credentials))
            .where(User.id == user_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Fetch user by unique email address (case-insensitive lookup)."""
        stmt = (
            select(User)
            .options(selectinload(User.credentials))
            .where(User.email.ilike(email.strip()))
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_user(
        self,
        email: str,
        full_name: str,
        is_active: bool = True,
        is_superuser: bool = False,
    ) -> User:
        """Create a new user record."""
        user = User(
            email=email.strip().lower(),
            full_name=full_name.strip(),
            is_active=is_active,
            is_superuser=is_superuser,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def create_credentials(
        self,
        user_id: uuid.UUID,
        password_hash: str,
    ) -> UserCredentials:
        """Create credentials record linked to a user."""
        creds = UserCredentials(
            user_id=user_id,
            password_hash=password_hash,
        )
        self.session.add(creds)
        await self.session.flush()
        return creds


class SessionRepository:
    """Persistence operations for Session entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_session(
        self,
        user_id: uuid.UUID,
        token_hash: str,
        csrf_token: str,
        expires_at: datetime,
        user_agent: str | None = None,
        ip_address: str | None = None,
    ) -> Session:
        """Persist a new active session."""
        db_session = Session(
            user_id=user_id,
            token_hash=token_hash,
            csrf_token=csrf_token,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self.session.add(db_session)
        await self.session.flush()
        return db_session

    async def get_by_token_hash(self, token_hash: str) -> Session | None:
        """Fetch active session by hashed token with eager-loaded user."""
        stmt = (
            select(Session)
            .options(selectinload(Session.user))
            .where(Session.token_hash == token_hash)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def delete_by_token_hash(self, token_hash: str) -> bool:
        """Delete session matching token hash."""
        stmt = delete(Session).where(Session.token_hash == token_hash)
        result = await self.session.execute(stmt)
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount > 0)

    async def delete_all_user_sessions(self, user_id: uuid.UUID) -> int:
        """Revoke all sessions for a user."""
        stmt = delete(Session).where(Session.user_id == user_id)
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0))

    async def delete_expired(self) -> int:
        """Purge all expired sessions."""
        now = datetime.now(UTC)
        stmt = delete(Session).where(Session.expires_at <= now)
        result = await self.session.execute(stmt)
        return int(getattr(result, "rowcount", 0))
