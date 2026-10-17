from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings
from taskflow.domains.identity.models import Session, User
from taskflow.domains.identity.service import hash_token


@pytest.mark.asyncio
async def test_register_success(async_client: AsyncClient):
    """Test successful user registration returns user DTO and does not issue cookies."""
    payload = {
        "email": "alice@example.com",
        "password": "Password123!",
        "full_name": "Alice Smith",
    }
    response = await async_client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["message"] == "User registered successfully"
    assert data["user"]["email"] == "alice@example.com"
    assert data["user"]["full_name"] == "Alice Smith"
    assert data["user"]["is_active"] is True
    assert "id" in data["user"]
    assert "csrf_token" not in data

    # Verify no authentication cookies are set
    assert settings.SESSION_COOKIE_NAME not in response.cookies
    assert settings.CSRF_COOKIE_NAME not in response.cookies


@pytest.mark.asyncio
async def test_registration_does_not_grant_access(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Verify registration does not create sessions or grant access."""
    reg_payload = {
        "email": "unauthenticated_reg@example.com",
        "password": "Password123!",
        "full_name": "Unauth Reg",
    }
    reg_resp = await async_client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_resp.status_code == 201
    user_id = reg_resp.json()["user"]["id"]

    # 1. Protected endpoint /api/v1/auth/me rejects immediately
    me_resp = await async_client.get("/api/v1/auth/me")
    assert me_resp.status_code == 401
    assert me_resp.json()["error"]["code"] == "UNAUTHORIZED"

    # 2. Verify no active session row was created in database
    import uuid

    stmt = select(Session).where(Session.user_id == uuid.UUID(user_id))
    result = await db_session.execute(stmt)
    assert result.scalars().first() is None

    # 3. User can log in to obtain session
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={
            "email": "unauthenticated_reg@example.com",
            "password": "Password123!",
        },
    )
    assert login_resp.status_code == 200
    assert settings.SESSION_COOKIE_NAME in login_resp.cookies
    session_cookie = login_resp.cookies[settings.SESSION_COOKIE_NAME]

    # 4. Now /api/v1/auth/me succeeds with the login session cookie
    authed_me_resp = await async_client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_cookie},
    )
    assert authed_me_resp.status_code == 200
    assert authed_me_resp.json()["email"] == "unauthenticated_reg@example.com"


@pytest.mark.asyncio
async def test_register_duplicate_email(async_client: AsyncClient):
    """Test duplicate registration returns 409 EMAIL_ALREADY_EXISTS."""
    payload = {
        "email": "duplicate@example.com",
        "password": "Password123!",
        "full_name": "Original User",
    }
    resp1 = await async_client.post("/api/v1/auth/register", json=payload)
    assert resp1.status_code == 201

    resp2 = await async_client.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code == 409
    data = resp2.json()
    assert data["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    assert "X-Request-ID" in resp2.headers


@pytest.mark.asyncio
async def test_register_validation_errors(async_client: AsyncClient):
    """Test input validation for invalid email and short password."""
    # Invalid email
    resp1 = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "Password123!", "full_name": "Test"},
    )
    assert resp1.status_code == 422
    assert resp1.json()["error"]["code"] == "VALIDATION_ERROR"

    # Password too short (< 8 chars)
    resp2 = await async_client.post(
        "/api/v1/auth/register",
        json={"email": "valid@example.com", "password": "short", "full_name": "Test"},
    )
    assert resp2.status_code == 422
    assert resp2.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_register_disabled_policy(async_client: AsyncClient):
    """Test registration when public registration is disabled."""
    settings.ENABLE_PUBLIC_REGISTRATION = False
    try:
        response = await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": "denied@example.com",
                "password": "Password123!",
                "full_name": "Denied User",
            },
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "REGISTRATION_DISABLED"
    finally:
        settings.ENABLE_PUBLIC_REGISTRATION = True


@pytest.mark.asyncio
async def test_login_success(async_client: AsyncClient):
    """Test login with valid credentials sets cookies and returns auth response."""
    # Register first
    await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "bob@example.com",
            "password": "SecurePassword123!",
            "full_name": "Bob Jones",
        },
    )

    # Login
    response = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "bob@example.com", "password": "SecurePassword123!"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["message"] == "Authenticated successfully"
    assert data["user"]["email"] == "bob@example.com"
    assert "csrf_token" in data
    assert settings.SESSION_COOKIE_NAME in response.cookies


@pytest.mark.asyncio
async def test_login_invalid_credentials(async_client: AsyncClient):
    """Test login with wrong password or non-existent user returns 401."""
    # Register user
    await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "charlie@example.com",
            "password": "CorrectPassword123!",
            "full_name": "Charlie",
        },
    )

    # Wrong password
    resp1 = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "charlie@example.com", "password": "WrongPassword!"},
    )
    assert resp1.status_code == 401
    assert resp1.json()["error"]["code"] == "INVALID_CREDENTIALS"

    # Non-existent email
    resp2 = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "nonexistent@example.com", "password": "AnyPassword123!"},
    )
    assert resp2.status_code == 401
    assert resp2.json()["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_inactive_user(async_client: AsyncClient, db_session: AsyncSession):
    """Test login for deactivated user returns 403 USER_INACTIVE."""
    reg_resp = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "inactive@example.com",
            "password": "Password123!",
            "full_name": "Inactive User",
        },
    )
    user_id = reg_resp.json()["user"]["id"]

    # Deactivate user in test DB
    import uuid

    user = await db_session.get(User, uuid.UUID(user_id))
    assert user is not None
    user.is_active = False
    await db_session.commit()

    # Attempt login
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "inactive@example.com", "password": "Password123!"},
    )
    assert login_resp.status_code == 403
    assert login_resp.json()["error"]["code"] == "USER_INACTIVE"


@pytest.mark.asyncio
async def test_get_current_user_unauthenticated(async_client: AsyncClient):
    """Test /api/v1/auth/me returns 401 when unauthenticated."""
    response = await async_client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_get_current_user_with_cookie(async_client: AsyncClient):
    """Test /api/v1/auth/me succeeds with session cookie."""
    await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "david@example.com",
            "password": "Password123!",
            "full_name": "David Miller",
        },
    )
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "david@example.com", "password": "Password123!"},
    )
    session_cookie = login_resp.cookies[settings.SESSION_COOKIE_NAME]

    response = await async_client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_cookie},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "david@example.com"
    assert data["full_name"] == "David Miller"


@pytest.mark.asyncio
async def test_get_current_user_with_bearer(async_client: AsyncClient):
    """Test /api/v1/auth/me succeeds with Bearer token header."""
    await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "eva@example.com",
            "password": "Password123!",
            "full_name": "Eva Green",
        },
    )
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "eva@example.com", "password": "Password123!"},
    )
    session_token = login_resp.cookies[settings.SESSION_COOKIE_NAME]

    response = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "eva@example.com"


@pytest.mark.asyncio
async def test_get_current_user_expired_session(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Test expired session returns 401."""
    await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "expired@example.com",
            "password": "Password123!",
            "full_name": "Expired User",
        },
    )
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "expired@example.com", "password": "Password123!"},
    )
    session_cookie = login_resp.cookies[settings.SESSION_COOKIE_NAME]

    # Expire session in test DB
    token_digest = hash_token(session_cookie)
    stmt = select(Session).where(Session.token_hash == token_digest)
    res = await db_session.execute(stmt)
    db_sess = res.scalar_one()
    db_sess.expires_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.commit()

    response = await async_client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_cookie},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_logout_with_csrf_protection(async_client: AsyncClient):
    """Test logout requires CSRF header when authenticated via cookie."""
    await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "frank@example.com",
            "password": "Password123!",
            "full_name": "Frank White",
        },
    )
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "frank@example.com", "password": "Password123!"},
    )
    session_cookie = login_resp.cookies[settings.SESSION_COOKIE_NAME]
    csrf_token = login_resp.json()["csrf_token"]

    # 1. Attempt logout without CSRF header (should be rejected with 403)
    resp_no_csrf = await async_client.post(
        "/api/v1/auth/logout",
        cookies={settings.SESSION_COOKIE_NAME: session_cookie},
    )
    assert resp_no_csrf.status_code == 403
    assert resp_no_csrf.json()["error"]["code"] == "CSRF_TOKEN_INVALID"

    # 2. Logout with valid CSRF header
    resp_logout = await async_client.post(
        "/api/v1/auth/logout",
        cookies={settings.SESSION_COOKIE_NAME: session_cookie},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert resp_logout.status_code == 200
    assert resp_logout.json()["message"] == "Successfully logged out"

    # 3. Subsequent /auth/me must return 401
    resp_me = await async_client.get(
        "/api/v1/auth/me",
        cookies={settings.SESSION_COOKIE_NAME: session_cookie},
    )
    assert resp_me.status_code == 401


@pytest.mark.asyncio
async def test_logout_with_bearer_token(async_client: AsyncClient):
    """Test logout succeeds with Bearer token without needing CSRF header."""
    await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "grace@example.com",
            "password": "Password123!",
            "full_name": "Grace Hopper",
        },
    )
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "grace@example.com", "password": "Password123!"},
    )
    session_token = login_resp.cookies[settings.SESSION_COOKIE_NAME]

    logout_resp = await async_client.post(
        "/api/v1/auth/logout",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert logout_resp.status_code == 200

    # Verify session is revoked
    me_resp = await async_client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {session_token}"},
    )
    assert me_resp.status_code == 401
