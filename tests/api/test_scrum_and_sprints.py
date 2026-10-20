from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from taskflow.core.config import settings


async def create_user_and_login(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "Test User",
) -> tuple[dict, dict, dict]:
    """Create user and login, returning user_data, cookies, and headers."""
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
    org_name: str = "Scrum Org",
    org_slug: str = "scrum-org",
    proj_name: str = "Scrum Engine",
    proj_key: str = "SCRUM",
) -> tuple[str, str]:
    """Helper creating an organization and a project."""
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
        json={"name": proj_name, "key": proj_key, "is_private": False},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    return org_id, project_id


@pytest.mark.asyncio
async def test_backlog_crud_filtering_pagination_and_reorder(async_client: AsyncClient):
    """Test backlog listing, filters, story points summary, and reordering."""
    user, cookies, headers = await create_user_and_login(
        async_client, "backlog_lead@taskflow.dev"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client, cookies, headers, org_name="Backlog Org", org_slug="backlog-org"
    )

    # 1. Create 3 tasks with story points in the backlog (sprint_id is None)
    task1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Setup OAuth2 login",
            "priority": "high",
            "type": "story",
            "estimated_points": 5.0,
        },
        cookies=cookies,
        headers=headers,
    )
    assert task1_resp.status_code == 201
    task1 = task1_resp.json()

    task2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Design Database Schema",
            "priority": "urgent",
            "type": "task",
            "estimated_points": 8.0,
        },
        cookies=cookies,
        headers=headers,
    )
    assert task2_resp.status_code == 201
    task2 = task2_resp.json()

    task3_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Write Unit Tests",
            "priority": "low",
            "type": "task",
            "estimated_points": None,  # Unestimated
        },
        cookies=cookies,
        headers=headers,
    )
    assert task3_resp.status_code == 201
    task3 = task3_resp.json()

    # 2. Retrieve Product Backlog
    backlog_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/backlog",
        cookies=cookies,
        headers=headers,
    )
    assert backlog_resp.status_code == 200
    data = backlog_resp.json()
    assert data["total_tasks"] == 3
    assert data["total_points"] == 13.0  # 5 + 8
    assert data["unestimated_tasks"] == 1
    assert len(data["items"]) == 3

    # 3. Filter backlog by priority
    filter_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/backlog?priority=urgent",
        cookies=cookies,
        headers=headers,
    )
    assert filter_resp.status_code == 200
    filtered = filter_resp.json()
    assert filtered["total_tasks"] == 1
    assert filtered["items"][0]["id"] == task2["id"]

    # 4. Search backlog
    search_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/backlog?search=OAuth2",
        cookies=cookies,
        headers=headers,
    )
    assert search_resp.status_code == 200
    assert search_resp.json()["total_tasks"] == 1
    assert search_resp.json()["items"][0]["title"] == "Setup OAuth2 login"

    # 5. Reorder backlog using ordered_task_ids
    reorder_resp = await async_client.put(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/backlog/reorder",
        json={"ordered_task_ids": [task3["id"], task1["id"], task2["id"]]},
        cookies=cookies,
        headers=headers,
    )
    assert reorder_resp.status_code == 200
    reordered_items = reorder_resp.json()
    assert reordered_items[0]["id"] == task3["id"]
    assert reordered_items[1]["id"] == task1["id"]
    assert reordered_items[2]["id"] == task2["id"]


@pytest.mark.asyncio
async def test_sprint_crud_and_date_validations(async_client: AsyncClient):
    """Test Sprint creation, validation, update, and safe deletion."""
    user, cookies, headers = await create_user_and_login(
        async_client, "sprint_mgr@taskflow.dev"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client, cookies, headers, org_name="Sprint Org", org_slug="sprint-org"
    )

    now = datetime.now(UTC)
    start_date = now.isoformat()
    end_date = (now + timedelta(days=14)).isoformat()
    invalid_end_date = (now - timedelta(days=1)).isoformat()

    # 1. Invalid dates: end_date <= start_date rejected with 422
    invalid_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints",
        json={
            "name": "Invalid Sprint",
            "start_date": start_date,
            "end_date": invalid_end_date,
        },
        cookies=cookies,
        headers=headers,
    )
    assert invalid_resp.status_code in {400, 422}

    # 2. Valid sprint creation
    sprint_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints",
        json={
            "name": "Sprint 1",
            "goal": "Build foundation",
            "start_date": start_date,
            "end_date": end_date,
        },
        cookies=cookies,
        headers=headers,
    )
    assert sprint_resp.status_code == 201
    sprint = sprint_resp.json()
    assert sprint["name"] == "Sprint 1"
    assert sprint["status"] == "draft"
    assert sprint["goal"] == "Build foundation"
    sprint_id = sprint["id"]

    # 3. Retrieve sprint by ID
    get_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}",
        cookies=cookies,
        headers=headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == sprint_id

    # 4. Update sprint details
    update_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}",
        json={"name": "Sprint 1 - Updated", "goal": "Updated goal"},
        cookies=cookies,
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["name"] == "Sprint 1 - Updated"
    assert update_resp.json()["goal"] == "Updated goal"

    # 5. List sprints with status filter
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints?status=draft",
        cookies=cookies,
        headers=headers,
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    # 6. Delete draft sprint safely
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200
    assert "deleted" in del_resp.json()["message"].lower()

    # Verify 404 after deletion
    get_after = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}",
        cookies=cookies,
        headers=headers,
    )
    assert get_after.status_code == 404


@pytest.mark.asyncio
async def test_sprint_lifecycle_and_single_active_policy(async_client: AsyncClient):
    """Test start, active overlap prevention (409), and completion rollover."""
    user, cookies, headers = await create_user_and_login(
        async_client, "scrum_master@taskflow.dev"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client,
        cookies,
        headers,
        org_name="Lifecycle Org",
        org_slug="lifecycle-org",
    )

    # 1. Create two draft sprints
    s1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints",
        json={"name": "Sprint 1"},
        cookies=cookies,
        headers=headers,
    )
    assert s1_resp.status_code == 201
    sprint1_id = s1_resp.json()["id"]

    s2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints",
        json={"name": "Sprint 2"},
        cookies=cookies,
        headers=headers,
    )
    assert s2_resp.status_code == 201
    sprint2_id = s2_resp.json()["id"]

    # 2. Add two tasks to Sprint 1
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={"title": "Finished task", "estimated_points": 3.0},
        cookies=cookies,
        headers=headers,
    )
    task1 = t1_resp.json()

    t2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={"title": "Unfinished task", "estimated_points": 5.0},
        cookies=cookies,
        headers=headers,
    )
    task2 = t2_resp.json()

    assign_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint1_id}/tasks",
        json={"task_ids": [task1["id"], task2["id"]]},
        cookies=cookies,
        headers=headers,
    )
    assert assign_resp.status_code == 200

    # Mark task1 as completed ("done")
    status_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task1['id']}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )
    assert status_resp.status_code == 200

    # 3. Start Sprint 1
    start_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint1_id}/start",
        json={"goal": "Execute iteration 1"},
        cookies=cookies,
        headers=headers,
    )
    assert start_resp.status_code == 200
    assert start_resp.json()["status"] == "active"

    # 4. Attempt to start Sprint 2 while Sprint 1 is active -> 409 Conflict
    conflict_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint2_id}/start",
        json={},
        cookies=cookies,
        headers=headers,
    )
    assert conflict_resp.status_code == 409
    assert conflict_resp.json()["error"]["code"] == "ACTIVE_SPRINT_EXISTS"

    # 5. Complete Sprint 1 with rollover of unfinished tasks to Sprint 2
    comp_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint1_id}/complete",
        json={"move_unfinished_to": "next_sprint", "target_sprint_id": sprint2_id},
        cookies=cookies,
        headers=headers,
    )
    assert comp_resp.status_code == 200
    assert comp_resp.json()["status"] == "completed"

    # 6. Verify task1 (completed) remained in Sprint 1
    t1_check = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task1['id']}",
        cookies=cookies,
        headers=headers,
    )
    assert t1_check.json()["sprint_id"] == sprint1_id

    # 7. Verify task2 (unfinished) was rolled over into Sprint 2
    t2_check = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task2['id']}",
        cookies=cookies,
        headers=headers,
    )
    assert t2_check.json()["sprint_id"] == sprint2_id

    # 8. Sprint 1 is completed, so now Sprint 2 can be started without conflict
    start_s2 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint2_id}/start",
        json={},
        cookies=cookies,
        headers=headers,
    )
    assert start_s2.status_code == 200
    assert start_s2.json()["status"] == "active"


@pytest.mark.asyncio
async def test_cross_project_assignment_rejection(async_client: AsyncClient):
    """Test that tasks from Project B cannot be assigned to Sprints in Project A."""
    user, cookies, headers = await create_user_and_login(
        async_client, "cross_proj@taskflow.dev"
    )
    org_resp = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Multi Project Org", "slug": "multi-proj-org"},
        cookies=cookies,
        headers=headers,
    )
    org_id = org_resp.json()["id"]

    p1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Project One", "key": "PONE"},
        cookies=cookies,
        headers=headers,
    )
    p1_id = p1_resp.json()["id"]

    p2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Project Two", "key": "PTWO"},
        cookies=cookies,
        headers=headers,
    )
    p2_id = p2_resp.json()["id"]

    # Sprint in Project 1
    sprint_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{p1_id}/sprints",
        json={"name": "P1 Sprint"},
        cookies=cookies,
        headers=headers,
    )
    sprint_id = sprint_resp.json()["id"]

    # Task in Project 2
    task_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{p2_id}/tasks",
        json={"title": "Task in Project 2"},
        cookies=cookies,
        headers=headers,
    )
    task_id = task_resp.json()["id"]

    # Attempt to assign Project 2 task into Project 1 sprint -> 400 rejection
    assign_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{p1_id}/sprints/{sprint_id}/tasks",
        json={"task_ids": [task_id]},
        cookies=cookies,
        headers=headers,
    )
    assert assign_resp.status_code == 400
    assert assign_resp.json()["error"]["code"] == "CROSS_PROJECT_ASSIGNMENT_FORBIDDEN"


@pytest.mark.asyncio
async def test_story_point_estimation_and_audit_history(async_client: AsyncClient):
    """Test story point setting, validation, clearing, and audit logging."""
    user, cookies, headers = await create_user_and_login(
        async_client, "estimator@taskflow.dev"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client, cookies, headers, org_name="Estimate Org", org_slug="estimate-org"
    )

    task_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={"title": "Refactor Data Layer"},
        cookies=cookies,
        headers=headers,
    )
    task_id = task_resp.json()["id"]

    # 1. Invalid negative points -> 422
    neg_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task_id}/estimate",
        json={"estimated_points": -3.0},
        cookies=cookies,
        headers=headers,
    )
    assert neg_resp.status_code == 422

    # 2. Invalid excessive points > 1000 -> 422
    huge_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task_id}/estimate",
        json={"estimated_points": 1500.0},
        cookies=cookies,
        headers=headers,
    )
    assert huge_resp.status_code == 422

    # 3. Valid estimate update
    valid_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task_id}/estimate",
        json={"estimated_points": 5.0},
        cookies=cookies,
        headers=headers,
    )
    assert valid_resp.status_code == 200
    assert valid_resp.json()["estimated_points"] == 5.0

    # 4. Check audit activity recorded
    history_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task_id}/history",
        cookies=cookies,
        headers=headers,
    )
    assert history_resp.status_code == 200
    activities = history_resp.json()["items"]
    est_act = next((a for a in activities if a["field"] == "estimated_points"), None)
    assert est_act is not None
    assert est_act["new_value"] == "5.0"

    # 5. Clear estimate
    clear_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task_id}/estimate",
        json={"estimated_points": None},
        cookies=cookies,
        headers=headers,
    )
    assert clear_resp.status_code == 200
    assert clear_resp.json()["estimated_points"] is None


@pytest.mark.asyncio
async def test_sprint_summary_burndown_and_velocity_metrics(async_client: AsyncClient):
    """Test sprint summary aggregation, burndown time series, and velocity reports."""
    user, cookies, headers = await create_user_and_login(
        async_client, "metrics_lead@taskflow.dev"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client, cookies, headers, org_name="Metrics Org", org_slug="metrics-org"
    )

    now = datetime.now(UTC)
    start_date = (now - timedelta(days=5)).isoformat()
    end_date = (now + timedelta(days=5)).isoformat()

    # 1. Create and populate Sprint A
    sprint_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints",
        json={
            "name": "Metrics Sprint A",
            "start_date": start_date,
            "end_date": end_date,
        },
        cookies=cookies,
        headers=headers,
    )
    sprint_id = sprint_resp.json()["id"]

    # Create 3 tasks: 2 completed (3 + 5 pts), 1 incomplete (8 pts)
    t1 = (
        await async_client.post(
            f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
            json={"title": "Task 1", "estimated_points": 3.0, "sprint_id": sprint_id},
            cookies=cookies,
            headers=headers,
        )
    ).json()

    t2 = (
        await async_client.post(
            f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
            json={"title": "Task 2", "estimated_points": 5.0, "sprint_id": sprint_id},
            cookies=cookies,
            headers=headers,
        )
    ).json()

    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={"title": "Task 3", "estimated_points": 8.0, "sprint_id": sprint_id},
        cookies=cookies,
        headers=headers,
    )

    # Complete t1 and t2
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{t1['id']}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{t2['id']}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )

    # Start Sprint A
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}/start",
        json={},
        cookies=cookies,
        headers=headers,
    )

    # 2. Verify Sprint Summary
    summary_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}/summary",
        cookies=cookies,
        headers=headers,
    )
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total_task_count"] == 3
    assert summary["completed_task_count"] == 2
    assert summary["remaining_task_count"] == 1
    assert summary["total_estimated_points"] == 16.0
    assert summary["completed_points"] == 8.0
    assert summary["remaining_points"] == 8.0
    assert summary["completion_percentage_tasks"] == 66.7
    assert summary["completion_percentage_points"] == 50.0

    # 3. Verify Burndown time series
    burndown_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}/burndown",
        cookies=cookies,
        headers=headers,
    )
    assert burndown_resp.status_code == 200
    bd = burndown_resp.json()
    assert bd["total_story_points"] == 16.0
    assert bd["total_tasks"] == 3
    assert len(bd["data_points"]) > 0
    # First day has ideal points and data
    assert bd["data_points"][0]["ideal_story_points"] == 16.0
    assert bd["data_points"][0]["data_available"] is True

    # 4. Verify Project Velocity (currently no completed sprints, so count = 0)
    vel_initial = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/velocity",
        cookies=cookies,
        headers=headers,
    )
    assert vel_initial.status_code == 200
    assert vel_initial.json()["completed_sprints_count"] == 0
    assert vel_initial.json()["average_velocity_points"] == 0.0

    # Complete Sprint A
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}/complete",
        json={"move_unfinished_to": "backlog"},
        cookies=cookies,
        headers=headers,
    )

    # Verify Velocity includes completed sprint A with completed points = 8.0
    vel_after = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/velocity",
        cookies=cookies,
        headers=headers,
    )
    assert vel_after.status_code == 200
    vel_data = vel_after.json()
    assert vel_data["completed_sprints_count"] == 1
    assert vel_data["average_velocity_points"] == 8.0
    assert vel_data["average_velocity_tasks"] == 2.0
    assert len(vel_data["sprints"]) == 1
    assert vel_data["sprints"][0]["completed_points"] == 8.0


@pytest.mark.asyncio
async def test_sprint_retrospective_crud(async_client: AsyncClient):
    """Test creating and retrieving sprint retrospective notes."""
    user, cookies, headers = await create_user_and_login(
        async_client, "retro_lead@taskflow.dev"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client, cookies, headers, org_name="Retro Org", org_slug="retro-org"
    )

    sprint_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints",
        json={"name": "Retro Sprint"},
        cookies=cookies,
        headers=headers,
    )
    sprint_id = sprint_resp.json()["id"]

    # Initially None
    get_init = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}/retrospective",
        cookies=cookies,
        headers=headers,
    )
    assert get_init.status_code == 200
    assert get_init.json() is None

    # Save retrospective
    save_resp = await async_client.put(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}/retrospective",
        json={
            "what_went_well": "Async migrations executed cleanly.",
            "what_could_be_improved": "Fewer mid-sprint scope changes.",
            "action_items": "Lock scope once sprint is active.",
        },
        cookies=cookies,
        headers=headers,
    )
    assert save_resp.status_code == 200
    retro = save_resp.json()
    assert retro["what_went_well"] == "Async migrations executed cleanly."
    assert retro["action_items"] == "Lock scope once sprint is active."

    # Query back
    get_saved = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/sprints/{sprint_id}/retrospective",
        cookies=cookies,
        headers=headers,
    )
    assert get_saved.status_code == 200
    assert get_saved.json()["id"] == retro["id"]


@pytest.mark.asyncio
async def test_tenant_isolation_and_authorization(async_client: AsyncClient):
    """Verify that users from another org or viewers are restricted."""
    user1, cookies1, headers1 = await create_user_and_login(
        async_client, "tenant1@taskflow.dev"
    )
    org1_id, proj1_id = await setup_org_and_project(
        async_client, cookies1, headers1, org_name="Tenant 1 Org", org_slug="t1-org"
    )

    user2, cookies2, headers2 = await create_user_and_login(
        async_client, "tenant2@taskflow.dev"
    )

    # 1. User 2 attempts to view Org 1's backlog -> 403 Forbidden
    backlog_denied = await async_client.get(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/backlog",
        cookies=cookies2,
        headers=headers2,
    )
    assert backlog_denied.status_code == 403

    # 2. Add user 2 to Org 1 and Project 1 as a "viewer"
    add_org_member = await async_client.post(
        f"/api/v1/organizations/{org1_id}/members",
        json={"user_id": user2["id"], "role": "member"},
        cookies=cookies1,
        headers=headers1,
    )
    assert add_org_member.status_code == 201

    add_proj_member = await async_client.post(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/members",
        json={"user_id": user2["id"], "role": "viewer"},
        cookies=cookies1,
        headers=headers1,
    )
    assert add_proj_member.status_code == 201

    # 3. Viewer can view backlog
    backlog_allowed = await async_client.get(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/backlog",
        cookies=cookies2,
        headers=headers2,
    )
    assert backlog_allowed.status_code == 200

    # 4. Viewer CANNOT create a sprint -> 403 Forbidden
    create_sprint_denied = await async_client.post(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/sprints",
        json={"name": "Unauthorized Sprint"},
        cookies=cookies2,
        headers=headers2,
    )
    assert create_sprint_denied.status_code == 403
