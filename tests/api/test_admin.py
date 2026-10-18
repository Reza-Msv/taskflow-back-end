import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.cli.bootstrap_admin import create_or_promote_admin
from taskflow.core.config import settings
from taskflow.domains.identity.models import User


async def create_user_and_login(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "Test User",
    is_superuser: bool = False,
    db_session: AsyncSession | None = None,
) -> tuple[dict, dict, dict]:
    """Helper creating a user, optionally setting superuser, and logging in."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert reg_resp.status_code == 201
    user_data = reg_resp.json()["user"]

    if is_superuser and db_session is not None:
        stmt = select(User).where(User.id == uuid.UUID(user_data["id"]))
        res = await db_session.execute(stmt)
        db_user = res.scalar_one()
        db_user.is_superuser = True
        await db_session.commit()

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()

    session_cookie = login_resp.cookies[settings.SESSION_COOKIE_NAME]
    csrf_token = login_data["csrf_token"]

    cookies = {settings.SESSION_COOKIE_NAME: session_cookie}
    headers = {"X-CSRF-Token": csrf_token}

    return user_data, cookies, headers


# -------------------------------------------------------------
# Admin Authorization Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_endpoints_require_authentication(async_client: AsyncClient):
    """Unauthenticated requests to admin endpoints must return 401 UNAUTHORIZED."""
    resp = await async_client.get("/api/v1/admin/overview")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "UNAUTHORIZED"

    resp2 = await async_client.get("/api/v1/admin/users")
    assert resp2.status_code == 401

    resp3 = await async_client.get("/api/v1/admin/organizations")
    assert resp3.status_code == 401


@pytest.mark.asyncio
async def test_normal_user_denied_admin_access(
    async_client: AsyncClient, db_session: AsyncSession
):
    """A regular authenticated user must be denied admin access with 403 FORBIDDEN."""
    _, cookies, headers = await create_user_and_login(
        async_client,
        email="normal_user@example.com",
        is_superuser=False,
        db_session=db_session,
    )

    # 1. Admin overview
    resp = await async_client.get(
        "/api/v1/admin/overview",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"

    # 2. Admin users list
    resp = await async_client.get(
        "/api/v1/admin/users",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"

    # 3. Admin organizations list
    resp = await async_client.get(
        "/api/v1/admin/organizations",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.asyncio
async def test_admin_overview_metrics(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Admin user can view system-wide overview metrics."""
    _, admin_cookies, admin_headers = await create_user_and_login(
        async_client,
        email="admin_metrics@example.com",
        is_superuser=True,
        db_session=db_session,
    )

    # Register an additional regular user
    await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "user2@example.com",
            "password": "Password123!",
            "full_name": "User 2",
        },
    )

    resp = await async_client.get(
        "/api/v1/admin/overview",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_users"] >= 2
    assert data["active_users"] >= 2
    assert "total_organizations" in data
    assert "active_organizations" in data
    assert "total_projects" in data


# -------------------------------------------------------------
# User Management Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_list_users_pagination_and_search(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Admin can list users with pagination and search filters."""
    _, admin_cookies, admin_headers = await create_user_and_login(
        async_client,
        email="admin_list@example.com",
        is_superuser=True,
        db_session=db_session,
    )

    # Create distinct users
    for i in range(5):
        await async_client.post(
            "/api/v1/auth/register",
            json={
                "email": f"searchable_{i}@example.com",
                "password": "Password123!",
                "full_name": f"Searchable User {i}",
            },
        )

    # Pagination: page 1, size 2
    resp = await async_client.get(
        "/api/v1/admin/users?page=1&page_size=2",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 2
    assert data["page"] == 1
    assert data["page_size"] == 2
    assert data["total"] >= 6

    # Verify sensitive data is NEVER exposed
    first_user = data["items"][0]
    assert "password_hash" not in first_user
    assert "credentials" not in first_user
    assert "token" not in first_user
    assert "csrf_token" not in first_user

    # Search filter
    search_resp = await async_client.get(
        "/api/v1/admin/users?search=searchable_3",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert search_data["total"] == 1
    assert search_data["items"][0]["email"] == "searchable_3@example.com"


@pytest.mark.asyncio
async def test_admin_get_and_update_user_status(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Admin can view user details and activate/deactivate accounts."""
    _, admin_cookies, admin_headers = await create_user_and_login(
        async_client,
        email="admin_mgr@example.com",
        is_superuser=True,
        db_session=db_session,
    )

    # Register target user
    user_data, user_cookies, user_headers = await create_user_and_login(
        async_client,
        email="target_user@example.com",
        password="TargetPassword123!",
        full_name="Target User",
        is_superuser=False,
        db_session=db_session,
    )
    user_id = user_data["id"]

    # Get details
    get_resp = await async_client.get(
        f"/api/v1/admin/users/{user_id}",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["email"] == "target_user@example.com"
    assert "password" not in get_resp.json()

    # Deactivate target user
    patch_resp = await async_client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"is_active": False},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_active"] is False

    # Target user's active session must be rejected immediately
    me_resp = await async_client.get(
        "/api/v1/auth/me",
        cookies=user_cookies,
        headers=user_headers,
    )
    assert me_resp.status_code == 401 or me_resp.status_code == 403

    # Subsequent login attempt must be rejected
    login_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "target_user@example.com", "password": "TargetPassword123!"},
    )
    assert login_resp.status_code == 403
    assert login_resp.json()["error"]["code"] == "USER_INACTIVE"

    # Reactivate user
    reactivate_resp = await async_client.patch(
        f"/api/v1/admin/users/{user_id}",
        json={"is_active": True},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert reactivate_resp.status_code == 200
    assert reactivate_resp.json()["is_active"] is True

    # User can log in again
    relogin_resp = await async_client.post(
        "/api/v1/auth/login",
        json={"email": "target_user@example.com", "password": "TargetPassword123!"},
    )
    assert relogin_resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_or_demote_self(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Admin cannot deactivate or revoke superuser privileges from themselves."""
    admin_data, admin_cookies, admin_headers = await create_user_and_login(
        async_client,
        email="self_protect@example.com",
        is_superuser=True,
        db_session=db_session,
    )
    admin_id = admin_data["id"]

    # Try deactivating self
    deact_resp = await async_client.patch(
        f"/api/v1/admin/users/{admin_id}",
        json={"is_active": False},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert deact_resp.status_code == 400
    assert deact_resp.json()["error"]["code"] == "CANNOT_DEACTIVATE_SELF"

    # Try demoting self
    demote_resp = await async_client.patch(
        f"/api/v1/admin/users/{admin_id}",
        json={"is_superuser": False},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert demote_resp.status_code == 400
    assert demote_resp.json()["error"]["code"] == "CANNOT_DEMOTE_SELF"


# -------------------------------------------------------------
# Organization Management Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_organization_crud_and_validation(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Admin can create, list, view, and update organizations with input validation."""
    _, admin_cookies, admin_headers = await create_user_and_login(
        async_client,
        email="org_admin@example.com",
        is_superuser=True,
        db_session=db_session,
    )

    # Invalid slug validation failure (uppercase or symbols)
    invalid_resp = await async_client.post(
        "/api/v1/admin/organizations",
        json={"name": "Acme", "slug": "Invalid Slug!"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert invalid_resp.status_code == 422
    assert invalid_resp.json()["error"]["code"] == "VALIDATION_ERROR"

    # Create valid organization
    create_resp = await async_client.post(
        "/api/v1/admin/organizations",
        json={"name": "Acme Corporation", "slug": "acme-corp"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert create_resp.status_code == 201
    org = create_resp.json()
    org_id = org["id"]
    assert org["name"] == "Acme Corporation"
    assert org["slug"] == "acme-corp"
    assert org["is_active"] is True

    # Duplicate slug returns 409 Conflict
    dup_resp = await async_client.post(
        "/api/v1/admin/organizations",
        json={"name": "Duplicate Acme", "slug": "acme-corp"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error"]["code"] == "ORGANIZATION_SLUG_EXISTS"

    # Get by ID
    get_resp = await async_client.get(
        f"/api/v1/admin/organizations/{org_id}",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == org_id

    # List organizations
    list_resp = await async_client.get(
        "/api/v1/admin/organizations?search=acme",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    # Update organization name
    update_resp = await async_client.patch(
        f"/api/v1/admin/organizations/{org_id}",
        json={"name": "Acme Global"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["name"] == "Acme Global"


# -------------------------------------------------------------
# Membership Management & Boundary Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_organization_membership_management_and_role_protection(
    async_client: AsyncClient, db_session: AsyncSession
):
    """Admin can manage memberships and role assignments with last owner protection."""
    admin_data, admin_cookies, admin_headers = await create_user_and_login(
        async_client,
        email="membership_admin@example.com",
        is_superuser=True,
        db_session=db_session,
    )
    admin_id = uuid.UUID(admin_data["id"])

    # Create Organization (admin automatically assigned as initial owner)
    org_resp = await async_client.post(
        "/api/v1/admin/organizations",
        json={"name": "Member Org", "slug": "member-org"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    org_id = org_resp.json()["id"]

    # Register member candidate
    member_user_resp = await async_client.post(
        "/api/v1/auth/register",
        json={
            "email": "member_cand@example.com",
            "password": "Password123!",
            "full_name": "Member Candidate",
        },
    )
    member_user_id = member_user_resp.json()["user"]["id"]

    # Add member with role 'member'
    add_resp = await async_client.post(
        f"/api/v1/admin/organizations/{org_id}/members",
        json={"user_id": member_user_id, "role": "member"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert add_resp.status_code == 201
    assert add_resp.json()["role"] == "member"
    assert add_resp.json()["user"]["email"] == "member_cand@example.com"

    # Adding duplicate member returns 409 Conflict
    dup_member_resp = await async_client.post(
        f"/api/v1/admin/organizations/{org_id}/members",
        json={"user_id": member_user_id, "role": "admin"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert dup_member_resp.status_code == 409
    assert dup_member_resp.json()["error"]["code"] == "MEMBERSHIP_ALREADY_EXISTS"

    # Update role to 'admin'
    role_resp = await async_client.patch(
        f"/api/v1/admin/organizations/{org_id}/members/{member_user_id}",
        json={"role": "admin"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert role_resp.status_code == 200
    assert role_resp.json()["role"] == "admin"

    # List members
    list_members_resp = await async_client.get(
        f"/api/v1/admin/organizations/{org_id}/members",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert list_members_resp.status_code == 200
    assert list_members_resp.json()["total"] == 2

    # Attempt to remove the only owner (admin_id)
    # MUST be blocked by LAST_OWNER_PROTECTION
    remove_owner_resp = await async_client.delete(
        f"/api/v1/admin/organizations/{org_id}/members/{admin_id}",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert remove_owner_resp.status_code == 400
    assert remove_owner_resp.json()["error"]["code"] == "LAST_OWNER_PROTECTION"

    # Attempt to demote the only owner -> MUST be blocked
    demote_owner_resp = await async_client.patch(
        f"/api/v1/admin/organizations/{org_id}/members/{admin_id}",
        json={"role": "member"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert demote_owner_resp.status_code == 400
    assert demote_owner_resp.json()["error"]["code"] == "LAST_OWNER_PROTECTION"

    # Successfully remove the second member
    remove_member_resp = await async_client.delete(
        f"/api/v1/admin/organizations/{org_id}/members/{member_user_id}",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert remove_member_resp.status_code == 200
    assert "Member removed successfully" in remove_member_resp.json()["message"]


# -------------------------------------------------------------
# Bootstrap CLI Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_bootstrap_admin_cli(db_session: AsyncSession):
    """Test safe local bootstrap script creating or promoting administrator."""
    # Test creating new admin
    user = await create_or_promote_admin(
        email="cli_admin@example.com",
        password="BootstrapPassword123!",
        full_name="CLI Admin",
        session=db_session,
    )
    assert user.is_superuser is True
    assert user.is_active is True
    assert user.email == "cli_admin@example.com"

    # Test promoting existing normal user
    normal_user = await create_or_promote_admin(
        email="cli_promote@example.com",
        password="InitialPassword123!",
        full_name="To Promote",
        session=db_session,
    )
    # Demote normal_user first
    normal_user.is_superuser = False
    await db_session.commit()

    # Promote
    promoted_user = await create_or_promote_admin(
        email="cli_promote@example.com",
        session=db_session,
    )
    assert promoted_user.is_superuser is True
