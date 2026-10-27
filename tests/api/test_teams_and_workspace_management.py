import pytest
from httpx import AsyncClient

from taskflow.core.config import settings


async def create_user_and_login(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "Test User",
) -> tuple[dict, dict, dict]:
    """Helper creating user & logging in, returning user_data, cookies, and headers."""
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
# Workspace Stats, Member Search/Filter, CSV Export
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_workspace_stats_and_member_filtering(
    async_client: AsyncClient,
):
    """Test workspace stats, member search & role filter, and CSV export."""
    owner, owner_c, owner_h = await create_user_and_login(
        async_client, email="owner_stats@example.com", full_name="Alice Owner"
    )
    admin_u, _, _ = await create_user_and_login(
        async_client, email="admin_stats@example.com", full_name="Bob Admin"
    )
    member_u, _, _ = await create_user_and_login(
        async_client, email="member_stats@example.com", full_name="Charlie Member"
    )

    # 1. Create organization
    org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Analytics Corp", "slug": "analytics-corp"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert org_resp.status_code == 201
    org_id = org_resp.json()["id"]

    # 2. Add admin and member
    add_admin = await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": admin_u["id"], "role": "admin"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert add_admin.status_code == 201

    add_member = await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": member_u["id"], "role": "member"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert add_member.status_code == 201

    # 3. Create a project and team to test stats
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Core Project", "key": "COR"},
        cookies=owner_c,
        headers=owner_h,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/teams",
        json={"name": "Data Team", "description": "Big Data Analysts"},
        cookies=owner_c,
        headers=owner_h,
    )

    # 4. Check Workspace Stats
    stats_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/stats",
        cookies=owner_c,
        headers=owner_h,
    )
    assert stats_resp.status_code == 200
    stats = stats_resp.json()
    assert stats["organization_id"] == org_id
    assert stats["member_count"] == 3
    assert stats["active_owners_count"] == 1
    assert stats["team_count"] == 1
    assert stats["project_count"] == 1

    # 5. Member filtering by role
    admin_filter = await async_client.get(
        f"/api/v1/organizations/{org_id}/members?role=admin",
        cookies=owner_c,
        headers=owner_h,
    )
    assert admin_filter.status_code == 200
    admin_items = admin_filter.json()["items"]
    assert len(admin_items) == 1
    assert admin_items[0]["user_id"] == admin_u["id"]

    # 6. Member filtering by search query (name or email)
    search_charlie = await async_client.get(
        f"/api/v1/organizations/{org_id}/members?search=Charlie",
        cookies=owner_c,
        headers=owner_h,
    )
    assert search_charlie.status_code == 200
    charlie_items = search_charlie.json()["items"]
    assert len(charlie_items) == 1
    assert charlie_items[0]["user_id"] == member_u["id"]

    # 7. CSV Export
    csv_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/members/export",
        cookies=owner_c,
        headers=owner_h,
    )
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "attachment; filename=" in csv_resp.headers["content-disposition"]
    csv_text = csv_resp.text
    assert "Member ID,User ID,Full Name,Email,Role,Active,Joined At" in csv_text
    assert "Alice Owner" in csv_text
    assert "Bob Admin" in csv_text
    assert "Charlie Member" in csv_text


# -------------------------------------------------------------
# Ownership Transfer & Last-Owner Protection
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_ownership_transfer_and_safety(
    async_client: AsyncClient,
):
    """Test primary ownership transfer and enforce last-owner protection."""
    owner, owner_c, owner_h = await create_user_and_login(
        async_client, email="old_owner@example.com", full_name="Old Owner"
    )
    successor, succ_c, succ_h = await create_user_and_login(
        async_client, email="successor@example.com", full_name="New Owner"
    )
    outsider, _, _ = await create_user_and_login(
        async_client, email="outsider_transfer@example.com", full_name="Outsider"
    )

    org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Transfer Corp", "slug": "transfer-corp"},
        cookies=owner_c,
        headers=owner_h,
    )
    org_id = org_resp.json()["id"]

    # Add successor as regular member
    await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": successor["id"], "role": "member"},
        cookies=owner_c,
        headers=owner_h,
    )

    # 1. Non-owner cannot transfer ownership
    forbidden_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/transfer-ownership",
        json={"new_owner_id": successor["id"]},
        cookies=succ_c,
        headers=succ_h,
    )
    assert forbidden_resp.status_code == 403

    # 2. Cannot transfer to self
    self_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/transfer-ownership",
        json={"new_owner_id": owner["id"]},
        cookies=owner_c,
        headers=owner_h,
    )
    assert self_resp.status_code == 400
    assert self_resp.json()["error"]["code"] == "INVALID_OWNERSHIP_TRANSFER"

    # 3. Cannot transfer to outsider not in org
    outsider_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/transfer-ownership",
        json={"new_owner_id": outsider["id"]},
        cookies=owner_c,
        headers=owner_h,
    )
    assert outsider_resp.status_code == 404
    assert outsider_resp.json()["error"]["code"] == "TARGET_MEMBER_NOT_FOUND"

    # 4. Valid ownership transfer
    transfer_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/transfer-ownership",
        json={"new_owner_id": successor["id"]},
        cookies=owner_c,
        headers=owner_h,
    )
    assert transfer_resp.status_code == 200
    transferred = transfer_resp.json()
    assert transferred["user_id"] == successor["id"]
    assert transferred["role"] == "owner"

    # Verify former owner is now 'admin'
    members_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/members",
        cookies=succ_c,
        headers=succ_h,
    )
    members = members_resp.json()["items"]
    old_owner_record = next(m for m in members if m["user_id"] == owner["id"])
    assert old_owner_record["role"] == "admin"

    # 5. Last owner protection on new owner
    demote_fail = await async_client.patch(
        f"/api/v1/organizations/{org_id}/members/{successor['id']}",
        json={"role": "member"},
        cookies=succ_c,
        headers=succ_h,
    )
    assert demote_fail.status_code == 400
    assert demote_fail.json()["error"]["code"] == "LAST_OWNER_PROTECTION"


# -------------------------------------------------------------
# Team Management & Workspace Boundary Checks
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_crud_and_boundary_checks(
    async_client: AsyncClient,
):
    """Test Team CRUD, workspace member boundary checks, and member management."""
    owner, owner_c, owner_h = await create_user_and_login(
        async_client, email="team_owner@example.com", full_name="Team Owner"
    )
    member_u, member_c, member_h = await create_user_and_login(
        async_client, email="team_member@example.com", full_name="Team Member"
    )
    outsider, _, _ = await create_user_and_login(
        async_client, email="outsider_team@example.com", full_name="Outsider"
    )

    org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Team Space", "slug": "team-space"},
        cookies=owner_c,
        headers=owner_h,
    )
    org_id = org_resp.json()["id"]

    # Add member_u to organization
    await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": member_u["id"], "role": "member"},
        cookies=owner_c,
        headers=owner_h,
    )

    # 1. Create Team
    create_team_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/teams",
        json={"name": "Platform Infrastructure", "description": "Cloud & Infra"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert create_team_resp.status_code == 201
    team = create_team_resp.json()
    assert team["name"] == "Platform Infrastructure"
    assert team["member_count"] == 0
    team_id = team["id"]

    # 2. Duplicate team name in same org fails
    dup_team = await async_client.post(
        f"/api/v1/organizations/{org_id}/teams",
        json={"name": "Platform Infrastructure"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert dup_team.status_code == 409
    assert dup_team.json()["error"]["code"] == "TEAM_NAME_EXISTS"

    # 3. Workspace Boundary: attempt to add outsider to team fails
    boundary_fail = await async_client.post(
        f"/api/v1/organizations/{org_id}/teams/{team_id}/members",
        json={"user_id": outsider["id"], "role": "member"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert boundary_fail.status_code == 400
    assert boundary_fail.json()["error"]["code"] == "USER_NOT_ORGANIZATION_MEMBER"

    # 4. Add valid organization member to team
    add_m_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/teams/{team_id}/members",
        json={"user_id": member_u["id"], "role": "lead"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert add_m_resp.status_code == 201
    tm = add_m_resp.json()
    assert tm["user_id"] == member_u["id"]
    assert tm["role"] == "lead"
    assert tm["user"]["email"] == "team_member@example.com"

    # 5. Adding duplicate team member fails
    dup_tm = await async_client.post(
        f"/api/v1/organizations/{org_id}/teams/{team_id}/members",
        json={"user_id": member_u["id"], "role": "member"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert dup_tm.status_code == 409
    assert dup_tm.json()["error"]["code"] == "TEAM_MEMBERSHIP_ALREADY_EXISTS"

    # 6. List Teams (verifies member_count is 1)
    list_t = await async_client.get(
        f"/api/v1/organizations/{org_id}/teams",
        cookies=member_c,
        headers=member_h,
    )
    assert list_t.status_code == 200
    assert list_t.json()["total"] == 1
    assert list_t.json()["items"][0]["member_count"] == 1

    # 7. Get Team Details
    detail_t = await async_client.get(
        f"/api/v1/organizations/{org_id}/teams/{team_id}",
        cookies=member_c,
        headers=member_h,
    )
    assert detail_t.status_code == 200
    detail = detail_t.json()
    assert detail["member_count"] == 1
    assert len(detail["members"]) == 1
    assert detail["members"][0]["user_id"] == member_u["id"]

    # 8. Update Team Member Role
    patch_tm = await async_client.patch(
        f"/api/v1/organizations/{org_id}/teams/{team_id}/members/{member_u['id']}",
        json={"role": "architect"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert patch_tm.status_code == 200
    assert patch_tm.json()["role"] == "architect"

    # 9. Member removes themselves from team (self-service leave)
    leave_t = await async_client.delete(
        f"/api/v1/organizations/{org_id}/teams/{team_id}/members/{member_u['id']}",
        cookies=member_c,
        headers=member_h,
    )
    assert leave_t.status_code == 200

    # Verify team is now empty
    empty_list = await async_client.get(
        f"/api/v1/organizations/{org_id}/teams/{team_id}/members",
        cookies=owner_c,
        headers=owner_h,
    )
    assert empty_list.json()["total"] == 0

    # 10. Update Team attributes
    update_t = await async_client.patch(
        f"/api/v1/organizations/{org_id}/teams/{team_id}",
        json={"name": "DevOps & Cloud", "description": "Updated charter"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert update_t.status_code == 200
    assert update_t.json()["name"] == "DevOps & Cloud"

    # 11. Delete Team
    del_t = await async_client.delete(
        f"/api/v1/organizations/{org_id}/teams/{team_id}",
        cookies=owner_c,
        headers=owner_h,
    )
    assert del_t.status_code == 200

    # Verify team is gone
    get_gone = await async_client.get(
        f"/api/v1/organizations/{org_id}/teams/{team_id}",
        cookies=owner_c,
        headers=owner_h,
    )
    assert get_gone.status_code == 404


# -------------------------------------------------------------
# Invitation Lifecycle (Public Check, Revoke, Resend, Accept)
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_invitation_extended_lifecycle(
    async_client: AsyncClient,
):
    """
    Test full invitation lifecycle: public details, revocation, resending,
    and acceptance.
    """
    owner, owner_c, owner_h = await create_user_and_login(
        async_client, email="inv_owner@example.com", full_name="Inviter Owner"
    )
    invitee, invitee_c, invitee_h = await create_user_and_login(
        async_client, email="future_member@example.com", full_name="Future Member"
    )

    org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Pioneer Labs", "slug": "pioneer-labs"},
        cookies=owner_c,
        headers=owner_h,
    )
    org_id = org_resp.json()["id"]

    # 1. Create invitation
    inv_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        json={"email": "future_member@example.com", "role": "member"},
        cookies=owner_c,
        headers=owner_h,
    )
    assert inv_resp.status_code == 201
    invitation = inv_resp.json()
    inv_id = invitation["id"]
    token1 = invitation["token"]
    assert invitation["status"] == "pending"

    # 2. Public invitation inspection without authentication
    public_resp = await async_client.get(
        f"/api/v1/organizations/invitations/details?token={token1}"
    )
    assert public_resp.status_code == 200
    pub_data = public_resp.json()
    assert pub_data["organization_name"] == "Pioneer Labs"
    assert pub_data["invited_by_name"] == "Inviter Owner"
    assert pub_data["email"] == "future_member@example.com"
    assert pub_data["role"] == "member"
    assert pub_data["is_valid"] is True
    assert pub_data["status"] == "pending"

    # 3. Revoke invitation
    revoke_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/invitations/{inv_id}/revoke",
        cookies=owner_c,
        headers=owner_h,
    )
    assert revoke_resp.status_code == 200
    assert revoke_resp.json()["status"] == "revoked"

    # 4. Public inspection shows revoked
    rev_pub = await async_client.get(
        f"/api/v1/organizations/invitations/details?token={token1}"
    )
    assert rev_pub.status_code == 200
    assert rev_pub.json()["is_valid"] is False
    assert rev_pub.json()["status"] == "revoked"

    # 5. Accepting revoked invitation fails
    accept_rev_fail = await async_client.post(
        f"/api/v1/organizations/invitations/accept?token={token1}",
        cookies=invitee_c,
        headers=invitee_h,
    )
    assert accept_rev_fail.status_code == 400
    assert accept_rev_fail.json()["error"]["code"] == "INVITATION_REVOKED"

    # 6. Resend invitation (generates new token, unrevokes)
    resend_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/invitations/{inv_id}/resend",
        cookies=owner_c,
        headers=owner_h,
    )
    assert resend_resp.status_code == 200
    new_inv = resend_resp.json()
    token2 = new_inv["token"]
    assert token2 != token1
    assert new_inv["status"] == "pending"

    # 7. List invitations with status filter
    pending_list = await async_client.get(
        f"/api/v1/organizations/{org_id}/invitations?status=pending",
        cookies=owner_c,
        headers=owner_h,
    )
    assert pending_list.status_code == 200
    assert len(pending_list.json()["items"]) == 1

    # 8. Accept new token successfully
    accept_resp = await async_client.post(
        f"/api/v1/organizations/invitations/accept?token={token2}",
        cookies=invitee_c,
        headers=invitee_h,
    )
    assert accept_resp.status_code == 200
    membership = accept_resp.json()
    assert membership["organization_id"] == org_id
    assert membership["user_id"] == invitee["id"]
    assert membership["role"] == "member"

    # 9. Trying to accept again fails
    double_accept = await async_client.post(
        f"/api/v1/organizations/invitations/accept?token={token2}",
        cookies=invitee_c,
        headers=invitee_h,
    )
    assert double_accept.status_code == 400
    assert double_accept.json()["error"]["code"] == "INVITATION_ALREADY_ACCEPTED"
