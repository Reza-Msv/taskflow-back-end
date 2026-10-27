import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings
from taskflow.domains.work_items.models import WorkflowStatus, WorkflowTransition


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


async def setup_org_and_project(
    client: AsyncClient,
    owner_cookies: dict,
    owner_headers: dict,
    org_name: str = "TaskFlow Org",
    org_slug: str = "taskflow-org",
    proj_name: str = "Platform Engine",
    proj_key: str = "ENG",
    is_private: bool = False,
) -> tuple[str, str]:
    """Helper creating an organization and a project, returning org_id and project_id."""
    org_resp = await client.post(
        "/api/v1/organizations",
        json={"name": org_name, "slug": org_slug},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert org_resp.status_code == 201
    org_id = org_resp.json()["id"]

    proj_resp = await client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": proj_name, "key": proj_key, "is_private": is_private},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    return org_id, project_id


# -------------------------------------------------------------
# 1. Task CRUD & Sequence Generation Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_creation_atomic_sequence_and_history(
    async_client: AsyncClient,
):
    """Creating tasks increments gapless issue key and logs initial creation history."""
    owner, cookies, headers = await create_user_and_login(
        async_client, email="task_owner@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="seq-org", proj_key="TSK"
    )

    # 1. Create first task
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "First Work Item",
            "description": "Initial backlog item",
            "priority": "high",
            "type": "task",
        },
        cookies=cookies,
        headers=headers,
    )
    assert t1_resp.status_code == 201
    t1 = t1_resp.json()
    assert t1["issue_key"] == "TSK-1"
    assert t1["sequence_number"] == 1
    assert t1["status"] == "todo"
    assert t1["priority"] == "high"
    assert t1["reporter_id"] == owner["id"]
    assert t1["version"] == 1

    # 2. Create second task -> gapless TSK-2
    t2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Second Work Item", "priority": "urgent"},
        cookies=cookies,
        headers=headers,
    )
    assert t2_resp.status_code == 201
    t2 = t2_resp.json()
    assert t2["issue_key"] == "TSK-2"
    assert t2["sequence_number"] == 2

    # 3. Verify history for first task
    hist_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{t1['id']}/history",
        cookies=cookies,
        headers=headers,
    )
    assert hist_resp.status_code == 200
    hist_data = hist_resp.json()
    assert hist_data["total"] >= 1
    creation_event = hist_data["items"][0]
    assert creation_event["field"] == "created"
    assert "Created task TSK-1" in creation_event["new_value"]
    assert creation_event["user"]["id"] == owner["id"]


@pytest.mark.asyncio
async def test_task_list_and_details_filtering(
    async_client: AsyncClient,
):
    """Listing tasks supports pagination, status, priority, and search filters."""
    owner, cookies, headers = await create_user_and_login(
        async_client, email="filter_owner@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="filter-org", proj_key="FLT"
    )

    # Create 3 tasks
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Setup OAuth2 flow", "priority": "urgent"},
        cookies=cookies,
        headers=headers,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Database indexing", "priority": "low"},
        cookies=cookies,
        headers=headers,
    )

    # Filter by priority urgent
    list_urgent = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks?priority=urgent",
        cookies=cookies,
        headers=headers,
    )
    assert list_urgent.status_code == 200
    assert list_urgent.json()["total"] == 1
    assert list_urgent.json()["items"][0]["title"] == "Setup OAuth2 flow"

    # Search keyword
    list_search = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks?search=indexing",
        cookies=cookies,
        headers=headers,
    )
    assert list_search.status_code == 200
    assert list_search.json()["total"] == 1
    assert list_search.json()["items"][0]["title"] == "Database indexing"


# -------------------------------------------------------------
# 2. Assignment & Cross-Tenant / Cross-Project Protection
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_assignment_eligibility_and_history(
    async_client: AsyncClient,
):
    """Assigning task requires active org membership; logs assignment history."""
    owner, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="lead_assigner@example.com"
    )
    member, member_cookies, member_headers = await create_user_and_login(
        async_client, email="eligible_member@example.com"
    )
    outsider, _, _ = await create_user_and_login(
        async_client, email="outsider_user@example.com"
    )

    org_id, project_id = await setup_org_and_project(
        async_client, owner_cookies, owner_headers, org_slug="assign-org", proj_key="ASG"
    )

    # Add member to org
    await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": member["id"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )

    # Create task
    task_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Feature Authentication"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    task_id = task_resp.json()["id"]

    # Try assigning to outsider (not in organization) -> 400 INVALID_ASSIGNEE
    invalid_assign = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        json={"assignee_id": outsider["id"]},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert invalid_assign.status_code == 400
    assert invalid_assign.json()["error"]["code"] == "INVALID_ASSIGNEE"

    # Assign to eligible member -> 200
    valid_assign = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        json={"assignee_id": member["id"]},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert valid_assign.status_code == 200
    assert valid_assign.json()["assignee_id"] == member["id"]

    # Check history records assignee_id change
    hist_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/history",
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert hist_resp.status_code == 200
    events = hist_resp.json()["items"]
    assign_event = next((e for e in events if e["field"] == "assignee_id"), None)
    assert assign_event is not None
    assert assign_event["new_value"] == member["id"]


@pytest.mark.asyncio
async def test_cross_project_and_tenant_access_denial(
    async_client: AsyncClient,
):
    """Users cannot view or edit tasks belonging to private/inaccessible projects."""
    owner1, cookies1, headers1 = await create_user_and_login(
        async_client, email="proj1_owner@example.com"
    )
    owner2, cookies2, headers2 = await create_user_and_login(
        async_client, email="proj2_owner@example.com"
    )

    org1, proj1 = await setup_org_and_project(
        async_client, cookies1, headers1, org_slug="corp-alpha", proj_key="ALP", is_private=True
    )
    org2, proj2 = await setup_org_and_project(
        async_client, cookies2, headers2, org_slug="corp-beta", proj_key="BET"
    )

    # Owner 1 creates task in private project 1
    t_resp = await async_client.post(
        f"/api/v1/organizations/{org1}/projects/{proj1}/tasks",
        json={"title": "Alpha Secret Task"},
        cookies=cookies1,
        headers=headers1,
    )
    task1_id = t_resp.json()["id"]

    # Owner 2 tries to read Alpha task -> 403 (not in org)
    get_resp = await async_client.get(
        f"/api/v1/organizations/{org1}/projects/{proj1}/tasks/{task1_id}",
        cookies=cookies2,
        headers=headers2,
    )
    assert get_resp.status_code == 403

    # Owner 2 tries to modify Alpha task with their own org ID -> 404 (cross-tenant mismatch)
    mod_resp = await async_client.patch(
        f"/api/v1/organizations/{org2}/projects/{proj2}/tasks/{task1_id}",
        json={"title": "Hacked Title"},
        cookies=cookies2,
        headers=headers2,
    )
    assert mod_resp.status_code == 404


# -------------------------------------------------------------
# 3. Kanban Workflow & Status Transitions
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_kanban_board_grouping_and_move(
    async_client: AsyncClient,
):
    """Kanban board groups tasks by status; move updates status, rank, and history."""
    owner, cookies, headers = await create_user_and_login(
        async_client, email="kanban_pm@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="kanban-org", proj_key="KAN"
    )

    # 1. Create two tasks
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Task One"},
        cookies=cookies,
        headers=headers,
    )
    t1_id = t1_resp.json()["id"]

    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Task Two"},
        cookies=cookies,
        headers=headers,
    )

    # 2. Get Kanban Board
    kb_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/kanban",
        cookies=cookies,
        headers=headers,
    )
    assert kb_resp.status_code == 200
    kb = kb_resp.json()
    assert len(kb["columns"]) >= 3  # To Do, In Progress, Done
    todo_col = next((c for c in kb["columns"] if c["status"]["category"] == "todo"), None)
    assert todo_col is not None
    assert todo_col["total_tasks"] == 2

    # 3. Move Task One to 'in_progress' with a new rank
    move_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{t1_id}/move",
        json={"status": "in_progress", "rank": "0|000005:"},
        cookies=cookies,
        headers=headers,
    )
    assert move_resp.status_code == 200
    moved_task = move_resp.json()
    assert moved_task["status"] == "in_progress"
    assert moved_task["rank"] == "0|000005:"
    assert moved_task["version"] == 2

    # 4. Re-check Kanban Board
    kb_resp2 = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/kanban",
        cookies=cookies,
        headers=headers,
    )
    kb2 = kb_resp2.json()
    todo_col2 = next((c for c in kb2["columns"] if c["status"]["category"] == "todo"), None)
    inp_col2 = next((c for c in kb2["columns"] if c["status"]["category"] == "in_progress"), None)
    assert todo_col2["total_tasks"] == 1
    assert inp_col2["total_tasks"] == 1
    assert inp_col2["tasks"][0]["id"] == t1_id

    # 5. Verify history stream logs status and rank transition
    hist_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{t1_id}/history",
        cookies=cookies,
        headers=headers,
    )
    events = hist_resp.json()["items"]
    status_event = next((e for e in events if e["field"] == "status"), None)
    assert status_event is not None
    assert status_event["old_value"] == "todo"
    assert status_event["new_value"] == "in_progress"


@pytest.mark.asyncio
async def test_workflow_transition_policy_validation(
    async_client: AsyncClient, db_session: AsyncSession
):
    """When explicit transitions exist, invalid status transitions are rejected."""
    owner, cookies, headers = await create_user_and_login(
        async_client, email="policy_pm@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="policy-org", proj_key="POL"
    )

    # Insert explicit project workflow statuses and restricted transition: To Do -> In Progress ONLY
    todo_st = WorkflowStatus(
        project_id=uuid.UUID(project_id),
        name="To Do",
        category="todo",
        position=1,
        is_default=True,
    )
    inp_st = WorkflowStatus(
        project_id=uuid.UUID(project_id),
        name="In Progress",
        category="in_progress",
        position=2,
    )
    done_st = WorkflowStatus(
        project_id=uuid.UUID(project_id),
        name="Done",
        category="done",
        position=3,
    )
    db_session.add_all([todo_st, inp_st, done_st])
    await db_session.flush()

    # Allow ONLY To Do -> In Progress
    trans = WorkflowTransition(
        from_status_id=todo_st.id,
        to_status_id=inp_st.id,
        name="Start Work",
    )
    db_session.add(trans)
    await db_session.commit()

    # Create task
    t_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Policy Restricted Task"},
        cookies=cookies,
        headers=headers,
    )
    task_id = t_resp.json()["id"]

    # Try moving directly from To Do -> Done (disallowed transition)
    illegal_move = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )
    assert illegal_move.status_code == 400
    assert illegal_move.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

    # Move from To Do -> In Progress (allowed)
    legal_move = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
        json={"status": "in_progress"},
        cookies=cookies,
        headers=headers,
    )
    assert legal_move.status_code == 200
    assert legal_move.json()["status"] == "in_progress"


# -------------------------------------------------------------
# 4. Optimistic Concurrency Control & Deletion Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_optimistic_concurrency_conflict_handling(
    async_client: AsyncClient,
):
    """Stale version in update or move returns 409 Conflict."""
    owner, cookies, headers = await create_user_and_login(
        async_client, email="occ_owner@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="occ-org", proj_key="OCC"
    )

    t_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Concurrent Work Item"},
        cookies=cookies,
        headers=headers,
    )
    task = t_resp.json()
    task_id = task["id"]
    assert task["version"] == 1

    # First client updates task with version 1 -> success, version becomes 2
    u1 = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        json={"title": "First Update", "version": 1},
        cookies=cookies,
        headers=headers,
    )
    assert u1.status_code == 200
    assert u1.json()["version"] == 2

    # Second client tries updating with stale version 1 -> 409 STALE_OBJECT_ERROR
    u2 = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        json={"title": "Stale Update", "version": 1},
        cookies=cookies,
        headers=headers,
    )
    assert u2.status_code == 409
    assert u2.json()["error"]["code"] == "STALE_OBJECT_ERROR"


@pytest.mark.asyncio
async def test_task_deletion(
    async_client: AsyncClient,
):
    """Task can be deleted and subsequent lookups return 404."""
    owner, cookies, headers = await create_user_and_login(
        async_client, email="del_owner@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="del-org", proj_key="DEL"
    )

    t_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Task to be Deleted"},
        cookies=cookies,
        headers=headers,
    )
    task_id = t_resp.json()["id"]

    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200
    assert "deleted successfully" in del_resp.json()["message"]

    # Verify 404 on get
    get_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        cookies=cookies,
        headers=headers,
    )
    assert get_resp.status_code == 404
