from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# Import centralized models registry to register all models with Base.metadata
import taskflow.db.models  # noqa: F401
from taskflow.api.deps import get_db
from taskflow.core.config import settings
from taskflow.db.base import Base
from taskflow.main import app

# Isolated in-memory database strictly for automated testing to
# guarantee zero mutation of real databases
TEST_DB_URL = "sqlite+aiosqlite:///file:testflow_memdb?mode=memory&cache=shared&uri=true"

test_engine = create_async_engine(
    TEST_DB_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

TestingSessionLocal = async_sessionmaker(
    bind=test_engine,
    class_=AsyncSession,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_settings():
    """Ensure public registration is enabled in test environment."""
    original_val = settings.ENABLE_PUBLIC_REGISTRATION
    settings.ENABLE_PUBLIC_REGISTRATION = True
    yield
    settings.ENABLE_PUBLIC_REGISTRATION = original_val


@pytest.fixture(autouse=True)
async def setup_test_db():
    """Create fresh schema for each test function and drop afterward."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency override providing an isolated test session."""
    async with TestingSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Test fixture providing an isolated session for direct DB assertions/mutations."""
    async with TestingSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


@pytest.fixture
async def async_client():
    """Async HTTP client wired to FastAPI app with overridden test database."""
    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        follow_redirects=True,
    ) as client:
        yield client
    app.dependency_overrides.clear()
