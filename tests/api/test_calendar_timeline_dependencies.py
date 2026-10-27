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
    """Helper creating user, returning user_data, cookies, and headers."""
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
    org_name: str = "Calendar Org",
    org_slug: str = "calendar-org",
    proj_name: str = "Gantt Project",
    proj_key: str = "GNT",
    is_private: bool = False,
) -> tuple[str, str]:
    """Helper creating org and project, returning org_id and project_id."""
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
# 1. Task Scheduling Fields & Date Validation
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_scheduling_start_date_and_validation(
    async_client: AsyncClient,
):
    """Verify planned start_date creation, update, and due_date validation."""
    _, cookies, headers = await create_user_and_login(
        async_client, email="sched_user@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="sched-org", proj_key="SCHED"
    )

    base_time = datetime.now(UTC)
    start_time = base_time + timedelta(days=2)
    due_time = base_time + timedelta(days=5)

    # 1. Valid task creation with start_date and due_date
    create_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Scheduled Task 1",
            "start_date": start_time.isoformat(),
            "due_date": due_time.isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    task_data = create_resp.json()
    assert task_data["start_date"] is not None
    assert task_data["due_date"] is not None
    task_id = task_data["id"]

    # 2. Reject creation where due_date < start_date
    invalid_create_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Invalid Date Task",
            "start_date": due_time.isoformat(),
            "due_date": start_time.isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert invalid_create_resp.status_code in {400, 422}

    # 3. Update task start_date and due_date validly
    new_start = base_time + timedelta(days=3)
    new_due = base_time + timedelta(days=6)
    update_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        json={
            "start_date": new_start.isoformat(),
            "due_date": new_due.isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["start_date"] is not None

    # 4. Update task sending due_date earlier than existing start_date
    bad_due_update = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        json={
            "due_date": (base_time + timedelta(days=1)).isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert bad_due_update.status_code in {400, 422}


# -------------------------------------------------------------
# 2. Project Milestones API Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_milestone_crud_and_task_metrics(
    async_client: AsyncClient,
):
    """Verify milestone CRUD, uniqueness, task counts, and deletion cascading."""
    _, cookies, headers = await create_user_and_login(
        async_client, email="milestone_user@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="mile-org", proj_key="MLE"
    )

    target_date = datetime.now(UTC) + timedelta(days=30)

    # 1. Create milestone
    m_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/milestones",
        json={
            "name": "Alpha Release",
            "description": "Initial customer preview",
            "target_date": target_date.isoformat(),
            "is_completed": False,
        },
        cookies=cookies,
        headers=headers,
    )
    assert m_resp.status_code == 201, m_resp.text
    m_data = m_resp.json()
    assert m_data["name"] == "Alpha Release"
    assert m_data["is_completed"] is False
    assert m_data["total_tasks"] == 0
    milestone_id = m_data["id"]

    # 2. Prevent duplicate milestone name in same project
    dup_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/milestones",
        json={
            "name": "Alpha Release",
            "target_date": target_date.isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert dup_resp.status_code == 409

    # 3. Create tasks associated with milestone
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Milestone Task 1",
            "milestone_id": milestone_id,
            "status": "todo",
        },
        cookies=cookies,
        headers=headers,
    )
    assert t1_resp.status_code == 201
    task1_id = t1_resp.json()["id"]

    t2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Milestone Task 2",
            "milestone_id": milestone_id,
            "status": "done",
        },
        cookies=cookies,
        headers=headers,
    )
    assert t2_resp.status_code == 201

    # 4. Verify milestone task counts
    get_m_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/milestones/{milestone_id}",
        cookies=cookies,
        headers=headers,
    )
    assert get_m_resp.status_code == 200
    assert get_m_resp.json()["total_tasks"] == 2
    assert get_m_resp.json()["completed_tasks"] == 1

    # 5. Verify task detail response includes milestone brief
    t1_detail = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task1_id}",
        cookies=cookies,
        headers=headers,
    )
    assert t1_detail.status_code == 200
    assert t1_detail.json()["milestone"] is not None
    assert t1_detail.json()["milestone"]["name"] == "Alpha Release"

    # 6. Update milestone (mark completed)
    up_m_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/milestones/{milestone_id}",
        json={"is_completed": True},
        cookies=cookies,
        headers=headers,
    )
    assert up_m_resp.status_code == 200
    assert up_m_resp.json()["is_completed"] is True

    # 7. List milestones with filter
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/milestones?is_completed=true",
        cookies=cookies,
        headers=headers,
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    # 8. Delete milestone
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/milestones/{milestone_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200

    # Task should still exist and milestone_id set to null
    t1_after = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task1_id}",
        cookies=cookies,
        headers=headers,
    )
    assert t1_after.status_code == 200
    assert t1_after.json()["milestone_id"] is None
    assert t1_after.json()["milestone"] is None


# -------------------------------------------------------------
# 3. Task Dependencies & DAG Cycle Prevention
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_dependencies_and_cycle_prevention(
    async_client: AsyncClient,
):
    """Verify task dependencies creation, duplicates, and cycle prevention."""
    _, cookies, headers = await create_user_and_login(
        async_client, email="dep_user@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="dep-org", proj_key="DEP"
    )

    # Create 3 tasks: A, B, C
    res_a = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Task A"},
        cookies=cookies,
        headers=headers,
    )
    assert res_a.status_code == 201
    task_a_id = res_a.json()["id"]

    res_b = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Task B"},
        cookies=cookies,
        headers=headers,
    )
    assert res_b.status_code == 201
    task_b_id = res_b.json()["id"]

    res_c = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Task C"},
        cookies=cookies,
        headers=headers,
    )
    assert res_c.status_code == 201
    task_c_id = res_c.json()["id"]

    # 1. Reject self-dependency (A -> A)
    self_dep_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={"predecessor_id": task_a_id, "successor_id": task_a_id},
        cookies=cookies,
        headers=headers,
    )
    assert self_dep_resp.status_code in {400, 422}

    # 2. Valid dependency: A -> B (B depends on A)
    dep1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={"predecessor_id": task_a_id, "successor_id": task_b_id},
        cookies=cookies,
        headers=headers,
    )
    assert dep1_resp.status_code == 201, dep1_resp.text
    dep1_data = dep1_resp.json()
    dep1_id = dep1_data["id"]

    # 3. Reject duplicate dependency (A -> B)
    dup_dep_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={"predecessor_id": task_a_id, "successor_id": task_b_id},
        cookies=cookies,
        headers=headers,
    )
    assert dup_dep_resp.status_code == 409

    # 4. Reject direct circular dependency (B -> A)
    direct_cycle_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={"predecessor_id": task_b_id, "successor_id": task_a_id},
        cookies=cookies,
        headers=headers,
    )
    assert direct_cycle_resp.status_code == 400
    assert direct_cycle_resp.json()["error"]["code"] == "CIRCULAR_DEPENDENCY"

    # 5. Valid dependency: B -> C (C depends on B)
    dep2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={"predecessor_id": task_b_id, "successor_id": task_c_id},
        cookies=cookies,
        headers=headers,
    )
    assert dep2_resp.status_code == 201

    # 6. Reject transitive circular dependency: C -> A (Chain: A -> B -> C -> A)
    transitive_cycle_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={"predecessor_id": task_c_id, "successor_id": task_a_id},
        cookies=cookies,
        headers=headers,
    )
    assert transitive_cycle_resp.status_code == 400
    assert transitive_cycle_resp.json()["error"]["code"] == "CIRCULAR_DEPENDENCY"

    # 7. Check task dependencies endpoints
    # Task B should have predecessor A and successor C
    task_b_deps = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_b_id}/dependencies",
        cookies=cookies,
        headers=headers,
    )
    assert task_b_deps.status_code == 200
    b_deps_data = task_b_deps.json()
    assert len(b_deps_data["predecessors"]) == 1
    assert b_deps_data["predecessors"][0]["predecessor_id"] == task_a_id
    assert len(b_deps_data["successors"]) == 1
    assert b_deps_data["successors"][0]["successor_id"] == task_c_id

    # 8. List project dependencies
    proj_deps = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        cookies=cookies,
        headers=headers,
    )
    assert proj_deps.status_code == 200
    assert proj_deps.json()["total"] == 2

    # 9. Delete dependency
    del_dep = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies/{dep1_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_dep.status_code == 200

    # After deleting A -> B, creating C -> A should now be allowed (chain was broken)
    allowed_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={"predecessor_id": task_c_id, "successor_id": task_a_id},
        cookies=cookies,
        headers=headers,
    )
    assert allowed_resp.status_code == 201


# -------------------------------------------------------------
# 4. Calendar Data API Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_calendar_view_filtering_and_span(
    async_client: AsyncClient,
):
    """Verify tasks spanning calendar range, filtering, and unscheduled."""
    user, cookies, headers = await create_user_and_login(
        async_client, email="cal_user@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="cal-org", proj_key="CAL"
    )

    base = datetime(2026, 5, 10, 0, 0, 0, tzinfo=UTC)

    # Task 1: Spans into window: May 5 - May 15
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Task 1 (Spanning Into)",
            "start_date": (base - timedelta(days=5)).isoformat(),
            "due_date": (base + timedelta(days=5)).isoformat(),
            "assignee_id": user["id"],
            "status": "in_progress",
        },
        cookies=cookies,
        headers=headers,
    )

    # Task 2: Inside window: May 12 - May 18
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Task 2 (Fully Inside)",
            "start_date": (base + timedelta(days=2)).isoformat(),
            "due_date": (base + timedelta(days=8)).isoformat(),
            "status": "todo",
        },
        cookies=cookies,
        headers=headers,
    )

    # Task 3: Outside window (Before): May 1 - May 5
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Task 3 (Before)",
            "start_date": (base - timedelta(days=9)).isoformat(),
            "due_date": (base - timedelta(days=5)).isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )

    # Task 4: Unscheduled (no dates)
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Task 4 (Unscheduled)"},
        cookies=cookies,
        headers=headers,
    )

    # Query calendar window: May 10 - May 20
    cal_start = base
    cal_end = base + timedelta(days=10)

    # Query without unscheduled
    cal_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/calendar",
        params={
            "start_date": cal_start.isoformat(),
            "end_date": cal_end.isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert cal_resp.status_code == 200, cal_resp.text
    cal_data = cal_resp.json()
    assert cal_data["total"] == 2
    assert len(cal_data["items"]) == 2
    assert len(cal_data["unscheduled_items"]) == 0

    # Query with unscheduled
    cal_with_unsched = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/calendar",
        params={
            "start_date": cal_start.isoformat(),
            "end_date": cal_end.isoformat(),
            "include_unscheduled": "true",
        },
        cookies=cookies,
        headers=headers,
    )
    assert cal_with_unsched.status_code == 200
    assert len(cal_with_unsched.json()["unscheduled_items"]) == 1
    assert (
        cal_with_unsched.json()["unscheduled_items"][0]["title"]
        == "Task 4 (Unscheduled)"
    )

    # Query with status filter
    cal_filter = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/calendar",
        params={
            "start_date": cal_start.isoformat(),
            "end_date": cal_end.isoformat(),
            "status": "in_progress",
        },
        cookies=cookies,
        headers=headers,
    )
    assert cal_filter.status_code == 200
    assert cal_filter.json()["total"] == 1
    assert cal_filter.json()["items"][0]["title"] == "Task 1 (Spanning Into)"

    # Invalid range: end_date < start_date
    cal_bad = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/calendar",
        params={
            "start_date": cal_end.isoformat(),
            "end_date": cal_start.isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert cal_bad.status_code == 400


# -------------------------------------------------------------
# 5. Timeline / Gantt Data API Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeline_gantt_data_payload(
    async_client: AsyncClient,
):
    """Verify Timeline endpoint aggregates tasks, milestones, and dependencies."""
    _, cookies, headers = await create_user_and_login(
        async_client, email="timeline_user@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="time-org", proj_key="TIM"
    )

    base = datetime.now(UTC)

    # 1. Create milestone
    m_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/milestones",
        json={
            "name": "Phase 1 Beta",
            "target_date": (base + timedelta(days=15)).isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert m_resp.status_code == 201
    m_id = m_resp.json()["id"]

    # 2. Create tasks (one with milestone, one without dates)
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Gantt Task 1",
            "start_date": base.isoformat(),
            "due_date": (base + timedelta(days=5)).isoformat(),
            "milestone_id": m_id,
        },
        cookies=cookies,
        headers=headers,
    )
    t1_id = t1_resp.json()["id"]

    t2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Gantt Task 2",
            "start_date": (base + timedelta(days=6)).isoformat(),
            "due_date": (base + timedelta(days=10)).isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    t2_id = t2_resp.json()["id"]

    t3_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Gantt Task 3 (No Dates)"},
        cookies=cookies,
        headers=headers,
    )
    assert t3_resp.status_code == 201

    # 3. Create dependency: T1 -> T2
    dep_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={"predecessor_id": t1_id, "successor_id": t2_id},
        cookies=cookies,
        headers=headers,
    )
    assert dep_resp.status_code == 201

    # 4. Fetch timeline
    timeline_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/timeline",
        cookies=cookies,
        headers=headers,
    )
    assert timeline_resp.status_code == 200, timeline_resp.text
    timeline_data = timeline_resp.json()

    assert "project" in timeline_data
    assert timeline_data["project"]["id"] == project_id
    assert len(timeline_data["tasks"]) == 3
    assert len(timeline_data["milestones"]) == 1
    assert len(timeline_data["dependencies"]) == 1

    # Verify task attributes in timeline
    t1_item = next(t for t in timeline_data["tasks"] if t["id"] == t1_id)
    assert t1_item["milestone_name"] == "Phase 1 Beta"
    assert t1_item["has_dates"] is True

    t3_item = next(t for t in timeline_data["tasks"] if t["id"] == t3_resp.json()["id"])
    assert t3_item["has_dates"] is False


# -------------------------------------------------------------
# 6. Cross-Project Isolation & Authorization Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_project_isolation_and_viewer_permissions(
    async_client: AsyncClient,
):
    """Verify cross-project resource rejection and viewer mutation blocking."""
    # 1. Setup owner with two projects
    _, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="iso_owner@example.com"
    )
    org_id, proj1_id = await setup_org_and_project(
        async_client, owner_cookies, owner_headers, org_slug="iso-org", proj_key="P1"
    )

    p2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Project Two", "key": "P2"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert p2_resp.status_code == 201
    proj2_id = p2_resp.json()["id"]

    # 2. Create tasks in each project
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj1_id}/tasks",
        json={"title": "Task in P1"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    t1_id = t1_resp.json()["id"]

    t2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj2_id}/tasks",
        json={"title": "Task in P2"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    t2_id = t2_resp.json()["id"]

    target_d = (datetime.now(UTC) + timedelta(days=10)).isoformat()
    m1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj1_id}/milestones",
        json={"name": "P1 Milestone", "target_date": target_d},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    m1_id = m1_resp.json()["id"]

    # 4. Attempt cross-project dependency: T1 (P1) -> T2 (P2) within P1
    cross_dep = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj1_id}/dependencies",
        json={"predecessor_id": t1_id, "successor_id": t2_id},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert cross_dep.status_code == 404

    # 5. Attempt cross-project milestone assignment: Assign M1 (P1) to T2 (P2)
    cross_m_task = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{proj2_id}/tasks/{t2_id}",
        json={"milestone_id": m1_id},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert cross_m_task.status_code == 404

    # 6. Add Viewer user to Org & Project 1
    viewer_user, viewer_cookies, viewer_headers = await create_user_and_login(
        async_client, email="iso_viewer@example.com"
    )
    # Add to org as member
    add_org_mem = await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": viewer_user["id"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert add_org_mem.status_code == 201

    # Add to Project 1 as viewer
    add_proj_mem = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj1_id}/members",
        json={"user_id": viewer_user["id"], "role": "viewer"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert add_proj_mem.status_code == 201

    # Viewer can view milestones
    view_m = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj1_id}/milestones",
        cookies=viewer_cookies,
        headers=viewer_headers,
    )
    assert view_m.status_code == 200

    # Viewer CANNOT create milestone (403)
    bad_create_m = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj1_id}/milestones",
        json={"name": "Viewer Milestone", "target_date": target_d},
        cookies=viewer_cookies,
        headers=viewer_headers,
    )
    assert bad_create_m.status_code == 403

    # Viewer CANNOT create dependency (403)
    t1_sub = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj1_id}/tasks",
        json={"title": "Task 1B"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    t1b_id = t1_sub.json()["id"]

    bad_create_dep = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj1_id}/dependencies",
        json={"predecessor_id": t1_id, "successor_id": t1b_id},
        cookies=viewer_cookies,
        headers=viewer_headers,
    )
    assert bad_create_dep.status_code == 403

