import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings


async def create_user_and_login(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "Test User",
) -> tuple[dict, dict, dict]:
    """Helper creating a user and logging in, returning user_data, cookies, and headers."""
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert reg_resp.status_code == 201
    user_data = reg_resp.json()["user"]

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
# Organizations Lifecycle & Access Control Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_organization_and_ownership(
    async_client: AsyncClient,
):
    """User creates an organization and automatically becomes owner."""
    user, cookies, headers = await create_user_and_login(
        async_client, email="owner1@example.com", full_name="Org Creator"
    )

    create_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Acme Ventures", "slug": "acme-ventures"},
        cookies=cookies,
        headers=headers,
    )
    assert create_resp.status_code == 201
    org = create_resp.json()
    assert org["name"] == "Acme Ventures"
    assert org["slug"] == "acme-ventures"
    assert org["is_active"] is True
    org_id = org["id"]

    # Verify membership list includes creator as owner
    members_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/members",
        cookies=cookies,
        headers=headers,
    )
    assert members_resp.status_code == 200
    members_data = members_resp.json()
    assert members_data["total"] == 1
    assert members_data["items"][0]["user_id"] == user["id"]
    assert members_data["items"][0]["role"] == "owner"


@pytest.mark.asyncio
async def test_list_my_organizations(
    async_client: AsyncClient,
):
    """User should only see organizations they belong to."""
    user1, cookies1, headers1 = await create_user_and_login(
        async_client, email="user_one@example.com"
    )
    user2, cookies2, headers2 = await create_user_and_login(
        async_client, email="user_two@example.com"
    )

    # User 1 creates Org A
    await async_client.post(
        "/api/v1/organizations",
        json={"name": "Org A", "slug": "org-a"},
        cookies=cookies1,
        headers=headers1,
    )

    # User 2 creates Org B
    await async_client.post(
        "/api/v1/organizations",
        json={"name": "Org B", "slug": "org-b"},
        cookies=cookies2,
        headers=headers2,
    )

    # User 1 lists orgs -> only Org A
    resp1 = await async_client.get(
        "/api/v1/organizations",
        cookies=cookies1,
        headers=headers1,
    )
    assert resp1.status_code == 200
    org_names1 = [o["name"] for o in resp1.json()["items"]]
    assert "Org A" in org_names1
    assert "Org B" not in org_names1

    # User 2 lists orgs -> only Org B
    resp2 = await async_client.get(
        "/api/v1/organizations",
        cookies=cookies2,
        headers=headers2,
    )
    assert resp2.status_code == 200
    org_names2 = [o["name"] for o in resp2.json()["items"]]
    assert "Org B" in org_names2
    assert "Org A" not in org_names2


@pytest.mark.asyncio
async def test_organization_cross_tenant_denial(
    async_client: AsyncClient,
):
    """Non-members cannot access, update, or delete other organizations."""
    _, cookies1, headers1 = await create_user_and_login(
        async_client, email="owner_isolated@example.com"
    )
    _, cookies2, headers2 = await create_user_and_login(
        async_client, email="stranger@example.com"
    )

    create_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Isolated Corp", "slug": "isolated-corp"},
        cookies=cookies1,
        headers=headers1,
    )
    org_id = create_resp.json()["id"]

    # Stranger tries to view details
    view_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}",
        cookies=cookies2,
        headers=headers2,
    )
    assert view_resp.status_code == 403
    assert view_resp.json()["error"]["code"] == "FORBIDDEN"

    # Stranger tries to update details
    update_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}",
        json={"name": "Hacked Corp"},
        cookies=cookies2,
        headers=headers2,
    )
    assert update_resp.status_code == 403

    # Stranger tries to delete org
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}",
        cookies=cookies2,
        headers=headers2,
    )
    assert del_resp.status_code == 403


@pytest.mark.asyncio
async def test_organization_update_and_delete_by_owner(
    async_client: AsyncClient,
):
    """Owner can update organization and delete it."""
    _, cookies, headers = await create_user_and_login(
        async_client, email="owner_updater@example.com"
    )

    create_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Old Name", "slug": "old-slug"},
        cookies=cookies,
        headers=headers,
    )
    org_id = create_resp.json()["id"]

    # Update name
    update_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}",
        json={"name": "New Name Corp"},
        cookies=cookies,
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["name"] == "New Name Corp"

    # Delete organization
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200
    assert "deleted successfully" in del_resp.json()["message"]


# -------------------------------------------------------------
# Organization Members & Permissions Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_member_and_role_escalation_prevention(
    async_client: AsyncClient,
):
    """Owner can add member; Admin cannot escalate themselves or others to owner."""
    owner, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="owner_hier@example.com"
    )
    admin_user, admin_cookies, admin_headers = await create_user_and_login(
        async_client, email="admin_hier@example.com"
    )
    regular_user, _, _ = await create_user_and_login(
        async_client, email="member_hier@example.com"
    )

    create_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Hierarchy Org", "slug": "hierarchy-org"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    org_id = create_resp.json()["id"]

    # Owner adds admin_user as admin
    add_admin_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": admin_user["id"], "role": "admin"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert add_admin_resp.status_code == 201
    assert add_admin_resp.json()["role"] == "admin"

    # Admin adds regular_user as member -> allowed
    add_member_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": regular_user["id"], "role": "member"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert add_member_resp.status_code == 201

    # Admin tries to add another user as owner -> 403 UNAUTHORIZED_ROLE_ESCALATION
    extra_user, _, _ = await create_user_and_login(
        async_client, email="extra_hier@example.com"
    )
    esc_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": extra_user["id"], "role": "owner"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert esc_resp.status_code == 403
    assert esc_resp.json()["error"]["code"] == "UNAUTHORIZED_ROLE_ESCALATION"

    # Admin tries to promote regular_user to owner -> 403 UNAUTHORIZED_ROLE_ESCALATION
    promote_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/members/{regular_user['id']}",
        json={"role": "owner"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert promote_resp.status_code == 403
    assert promote_resp.json()["error"]["code"] == "UNAUTHORIZED_ROLE_ESCALATION"

    # Admin tries to demote or remove the Owner -> 403 FORBIDDEN
    demote_owner_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/members/{owner['id']}",
        json={"role": "member"},
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert demote_owner_resp.status_code == 403

    del_owner_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/members/{owner['id']}",
        cookies=admin_cookies,
        headers=admin_headers,
    )
    assert del_owner_resp.status_code == 403


@pytest.mark.asyncio
async def test_last_owner_protection(
    async_client: AsyncClient,
):
    """Organization must prevent removing or demoting the last active owner."""
    owner, cookies, headers = await create_user_and_login(
        async_client, email="solo_owner@example.com"
    )
    create_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Solo Org", "slug": "solo-org"},
        cookies=cookies,
        headers=headers,
    )
    org_id = create_resp.json()["id"]

    # Try to demote self
    demote_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/members/{owner['id']}",
        json={"role": "member"},
        cookies=cookies,
        headers=headers,
    )
    assert demote_resp.status_code == 400
    assert demote_resp.json()["error"]["code"] == "LAST_OWNER_PROTECTION"

    # Try to remove self
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/members/{owner['id']}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 400
    assert del_resp.json()["error"]["code"] == "LAST_OWNER_PROTECTION"


@pytest.mark.asyncio
async def test_invitation_lifecycle(
    async_client: AsyncClient,
):
    """Test creating an invitation and accepting it by invitee."""
    _, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="inviter@example.com"
    )
    invitee, invitee_cookies, invitee_headers = await create_user_and_login(
        async_client, email="invitee@example.com"
    )

    create_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Invite Org", "slug": "invite-org"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    org_id = create_resp.json()["id"]

    # Send invitation
    inv_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        json={"email": "invitee@example.com", "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert inv_resp.status_code == 201
    invitation = inv_resp.json()
    assert invitation["email"] == "invitee@example.com"
    assert invitation["role"] == "member"
    token = invitation["token"]

    # Invitee accepts invitation
    accept_resp = await async_client.post(
        f"/api/v1/organizations/invitations/accept?token={token}",
        cookies=invitee_cookies,
        headers=invitee_headers,
    )
    assert accept_resp.status_code == 200
    membership = accept_resp.json()
    assert membership["organization_id"] == org_id
    assert membership["user_id"] == invitee["id"]
    assert membership["role"] == "member"

    # Verify membership list has 2 members
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/members",
        cookies=invitee_cookies,
        headers=invitee_headers,
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 2


# -------------------------------------------------------------
# Projects Lifecycle & Access Control Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_list_projects(
    async_client: AsyncClient,
):
    """Member can create a project, which enforces unique key per org."""
    user, cookies, headers = await create_user_and_login(
        async_client, email="proj_creator@example.com"
    )

    create_org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Project Org", "slug": "project-org"},
        cookies=cookies,
        headers=headers,
    )
    org_id = create_org_resp.json()["id"]

    # Create project 1
    p1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Frontend App", "key": "FE", "description": "Web app"},
        cookies=cookies,
        headers=headers,
    )
    assert p1_resp.status_code == 201
    p1 = p1_resp.json()
    assert p1["name"] == "Frontend App"
    assert p1["key"] == "FE"
    assert p1["owner_id"] == user["id"]
    assert p1["is_archived"] is False

    # Attempt to create duplicate key in same org -> 409
    dup_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Duplicate Key", "key": "FE"},
        cookies=cookies,
        headers=headers,
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error"]["code"] == "PROJECT_KEY_EXISTS"

    # List projects
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects",
        cookies=cookies,
        headers=headers,
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1
    assert list_resp.json()["items"][0]["key"] == "FE"


@pytest.mark.asyncio
async def test_project_privacy_and_access_denial(
    async_client: AsyncClient,
):
    """Private projects are invisible to org members unless explicitly assigned."""
    owner, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="privacy_owner@example.com"
    )
    member, member_cookies, member_headers = await create_user_and_login(
        async_client, email="privacy_member@example.com"
    )

    create_org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Privacy Org", "slug": "privacy-org"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    org_id = create_org_resp.json()["id"]

    # Add member to organization
    await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": member["id"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )

    # Owner creates private project
    p_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Top Secret", "key": "SEC", "is_private": True},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    project_id = p_resp.json()["id"]

    # Member lists projects -> SEC is private and member is not assigned, so total is 0
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects",
        cookies=member_cookies,
        headers=member_headers,
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 0

    # Member tries to get project by ID -> 404
    get_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}",
        cookies=member_cookies,
        headers=member_headers,
    )
    assert get_resp.status_code == 404
    assert get_resp.json()["error"]["code"] == "PROJECT_NOT_FOUND"


@pytest.mark.asyncio
async def test_project_members_management_and_isolation(
    async_client: AsyncClient,
):
    """Assign org member to project; prevent assigning non-org user."""
    owner, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="lead_pm@example.com"
    )
    org_dev, _, _ = await create_user_and_login(
        async_client, email="org_dev@example.com"
    )
    outsider, _, _ = await create_user_and_login(
        async_client, email="outsider@example.com"
    )

    create_org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Dev Org", "slug": "dev-org"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    org_id = create_org_resp.json()["id"]

    # Add org_dev to org
    await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": org_dev["id"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )

    # Create project
    p_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Backend Engine", "key": "BE"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    project_id = p_resp.json()["id"]

    # Add org_dev to project
    add_pm_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/members",
        json={"user_id": org_dev["id"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert add_pm_resp.status_code == 201
    assert add_pm_resp.json()["role"] == "member"

    # Try to add outsider (not in organization) -> 400 USER_NOT_IN_ORGANIZATION
    add_outsider_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/members",
        json={"user_id": outsider["id"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert add_outsider_resp.status_code == 400
    assert add_outsider_resp.json()["error"]["code"] == "USER_NOT_IN_ORGANIZATION"

    # Update role to admin
    update_role_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/members/{org_dev['id']}",
        json={"role": "admin"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert update_role_resp.status_code == 200
    assert update_role_resp.json()["role"] == "admin"

    # Remove member from project
    del_member_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/members/{org_dev['id']}",
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert del_member_resp.status_code == 200


@pytest.mark.asyncio
async def test_project_archival_and_deletion(
    async_client: AsyncClient,
):
    """Test soft archival and deletion of a project."""
    _, cookies, headers = await create_user_and_login(
        async_client, email="lifecycle_owner@example.com"
    )

    create_org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Lifecycle Org", "slug": "lifecycle-org"},
        cookies=cookies,
        headers=headers,
    )
    org_id = create_org_resp.json()["id"]

    p_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Archival Target", "key": "ARC"},
        cookies=cookies,
        headers=headers,
    )
    project_id = p_resp.json()["id"]

    # Archive project
    archive_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}",
        json={"is_archived": True},
        cookies=cookies,
        headers=headers,
    )
    assert archive_resp.status_code == 200
    assert archive_resp.json()["is_archived"] is True

    # List default active projects -> not listed
    list_active = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects",
        cookies=cookies,
        headers=headers,
    )
    assert list_active.status_code == 200
    assert list_active.json()["total"] == 0

    # List archived projects -> listed
    list_archived = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects?is_archived=true",
        cookies=cookies,
        headers=headers,
    )
    assert list_archived.status_code == 200
    assert list_archived.json()["total"] == 1

    # Delete project
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200
    assert "deleted successfully" in del_resp.json()["message"]
