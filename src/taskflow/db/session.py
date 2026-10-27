from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from taskflow.core.config import settings

engine_kwargs: dict[str, Any] = {
    "echo": settings.DEBUG,
    "future": True,
    "pool_pre_ping": True,
}
if not settings.DATABASE_URL.startswith("sqlite"):
    engine_kwargs.update(
        {
            "pool_size": settings.POSTGRES_POOL_SIZE,
            "max_overflow": settings.POSTGRES_MAX_OVERFLOW,
            "pool_timeout": settings.POSTGRES_POOL_TIMEOUT,
            "pool_recycle": settings.POSTGRES_POOL_RECYCLE,
        }
    )

engine = create_async_engine(
    settings.DATABASE_URL,
    **engine_kwargs,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
