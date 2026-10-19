import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings
from taskflow.domains.organizations.models import Organization, OrganizationMembership
from taskflow.domains.projects.models import Project, ProjectMembership
from taskflow.domains.work_items.models import WorkItem, WorkItemActivity


async def create_and_login_user(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "Test User",
) -> tuple[dict, dict, dict]:
    """Register and authenticate user, returning user info, cookies, and headers."""
    reg = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password, "full_name": full_name},
    )
    assert reg.status_code == 201
    user_data = reg.json()["user"]

    login = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200
    login_data = login.json()

    cookies = {
        settings.SESSION_COOKIE_NAME: login.cookies[settings.SESSION_COOKIE_NAME]
    }
    headers = {"X-CSRF-Token": login_data["csrf_token"]}
    return user_data, cookies, headers


@pytest.mark.asyncio
async def test_dashboard_endpoints_require_authentication(async_client: AsyncClient):
    """Ensure all dashboard endpoints reject unauthenticated requests with 401."""
    resp = await async_client.get("/api/v1/dashboard/summary")
    assert resp.status_code == 401

    resp = await async_client.get("/api/v1/dashboard/tasks")
    assert resp.status_code == 401

    resp = await async_client.get("/api/v1/dashboard/tasks/overdue-upcoming")
    assert resp.status_code == 401

    resp = await async_client.get("/api/v1/dashboard/activity")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_dashboard_empty_states(async_client: AsyncClient):
    """Verify that a brand new user sees cleanly populated empty states."""
    _, cookies, headers = await create_and_login_user(
        async_client, "newuser@taskflow.dev", full_name="New User"
    )

    # 1. Summary
    resp = await async_client.get(
        "/api/v1/dashboard/summary", cookies=cookies, headers=headers
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_projects"] == 0
    assert data["projects"] == []
    assert data["status_counts"] == {}
    assert data["assigned_tasks_count"] == 0
    assert data["in_progress_tasks_count"] == 0
    assert data["completed_tasks_count"] == 0
    assert data["overdue_tasks_count"] == 0

    # 2. Tasks
    resp = await async_client.get(
        "/api/v1/dashboard/tasks", cookies=cookies, headers=headers
    )
    assert resp.status_code == 200
    tasks_data = resp.json()
    assert tasks_data["items"] == []
    assert tasks_data["total"] == 0
    assert tasks_data["page"] == 1
    assert tasks_data["total_pages"] == 0

    # 3. Overdue and upcoming
    resp = await async_client.get(
        "/api/v1/dashboard/tasks/overdue-upcoming", cookies=cookies, headers=headers
    )
    assert resp.status_code == 200
    overdue_data = resp.json()
    assert overdue_data["overdue_tasks"] == []
    assert overdue_data["upcoming_tasks"] == []

    # 4. Activity
    resp = await async_client.get(
        "/api/v1/dashboard/activity", cookies=cookies, headers=headers
    )
    assert resp.status_code == 200
    act_data = resp.json()
    assert act_data["items"] == []
    assert act_data["total"] == 0


@pytest.mark.asyncio
async def test_dashboard_summary_and_metrics_calculation(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Verify dashboard summary correctly counts projects, status breakdown,
    and overdue tasks.
    """
    user_a, cookies_a, headers_a = await create_and_login_user(

        async_client, "alice@taskflow.dev", full_name="Alice"
    )
    user_b, _, _ = await create_and_login_user(
        async_client, "bob@taskflow.dev", full_name="Bob"
    )

    now = datetime.now(UTC)

    # Setup organization & memberships
    org = Organization(name="Acme Corp", slug="acme-corp")
    db_session.add(org)
    await db_session.flush()

    org_mem_a = OrganizationMembership(
        organization_id=org.id,
        user_id=uuid.UUID(user_a["id"]),
        role="member",
    )
    org_mem_b = OrganizationMembership(
        organization_id=org.id,
        user_id=uuid.UUID(user_b["id"]),
        role="member",
    )
    db_session.add_all([org_mem_a, org_mem_b])
    await db_session.flush()

    # Project 1: Public in org
    p1 = Project(
        organization_id=org.id,
        name="Platform Backend",
        key="BACK",
        is_private=False,
    )
    # Project 2: Private in org, Alice is explicit member
    p2 = Project(
        organization_id=org.id,
        name="Security Audit",
        key="SEC",
        is_private=True,
    )
    # Project 3: Private in org, Bob only (Alice has no access)
    p3 = Project(
        organization_id=org.id,
        name="Secret R&D",
        key="RND",
        is_private=True,
    )
    db_session.add_all([p1, p2, p3])
    await db_session.flush()

    p2_mem = ProjectMembership(
        project_id=p2.id,
        user_id=uuid.UUID(user_a["id"]),
        role="lead",
    )
    p3_mem = ProjectMembership(
        project_id=p3.id,
        user_id=uuid.UUID(user_b["id"]),
        role="owner",
    )
    db_session.add_all([p2_mem, p3_mem])
    await db_session.flush()

    # Create tasks for Alice in accessible projects
    # 1. Backlog item in P1 (todo, no due date)
    t1 = WorkItem(
        project_id=p1.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="BACK-1",
        title="Setup API scaffolding",
        status="todo",
        assignee_id=uuid.UUID(user_a["id"]),
    )
    # 2. In progress item in P1 (overdue!)
    t2 = WorkItem(
        project_id=p1.id,
        organization_id=org.id,
        sequence_number=2,
        issue_key="BACK-2",
        title="Database indexes",
        status="in_progress",
        due_date=now - timedelta(days=2),
        assignee_id=uuid.UUID(user_a["id"]),
    )
    # 3. Completed item in P1 (past due date, but completed so not overdue)
    t3 = WorkItem(
        project_id=p1.id,
        organization_id=org.id,
        sequence_number=3,
        issue_key="BACK-3",
        title="Auth middleware",
        status="done",
        due_date=now - timedelta(days=5),
        assignee_id=uuid.UUID(user_a["id"]),
    )
    # 4. Item in P2 (in_progress, future due date)
    t4 = WorkItem(
        project_id=p2.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="SEC-1",
        title="Vulnerability scan",
        status="in_progress",
        due_date=now + timedelta(days=3),
        assignee_id=uuid.UUID(user_a["id"]),
    )
    # 5. Item in P3 (Alice is assignee, but has NO project access
    # to P3 -> must be excluded)
    t5 = WorkItem(
        project_id=p3.id,

        organization_id=org.id,
        sequence_number=1,
        issue_key="RND-1",
        title="Secret prototype",
        status="todo",
        assignee_id=uuid.UUID(user_a["id"]),
    )
    # 6. Item in P1 assigned to Bob (not Alice)
    t6 = WorkItem(
        project_id=p1.id,
        organization_id=org.id,
        sequence_number=4,
        issue_key="BACK-4",
        title="Bob's task",
        status="todo",
        assignee_id=uuid.UUID(user_b["id"]),
    )
    db_session.add_all([t1, t2, t3, t4, t5, t6])
    await db_session.commit()

    # Call summary endpoint for Alice
    resp = await async_client.get(
        "/api/v1/dashboard/summary", cookies=cookies_a, headers=headers_a
    )
    assert resp.status_code == 200
    data = resp.json()

    # Projects: Alice should see P1 and P2, but NOT P3
    assert data["total_projects"] == 2
    proj_keys = [p["key"] for p in data["projects"]]
    assert "BACK" in proj_keys
    assert "SEC" in proj_keys
    assert "RND" not in proj_keys

    # Project summaries should report correct counts
    p1_summary = next(p for p in data["projects"] if p["key"] == "BACK")
    assert p1_summary["total_tasks"] == 4  # t1, t2, t3, t6
    assert p1_summary["assigned_tasks"] == 3  # t1, t2, t3

    p2_summary = next(p for p in data["projects"] if p["key"] == "SEC")
    assert p2_summary["total_tasks"] == 1
    assert p2_summary["assigned_tasks"] == 1
    assert p2_summary["role"] == "lead"

    # Task status and metrics (across accessible projects only: t1, t2, t3, t4)
    # t1: todo
    # t2: in_progress (overdue)
    # t3: done
    # t4: in_progress
    assert data["assigned_tasks_count"] == 4
    assert data["status_counts"]["todo"] == 1
    assert data["status_counts"]["in_progress"] == 2
    assert data["status_counts"]["done"] == 1

    assert data["in_progress_tasks_count"] == 2
    assert data["completed_tasks_count"] == 1
    assert data["overdue_tasks_count"] == 1  # Only t2


@pytest.mark.asyncio
async def test_my_tasks_pagination_filtering_and_smart_order(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """Verify pagination, filtering by status and project, and smart ordering."""
    user, cookies, headers = await create_and_login_user(
        async_client, "charlie@taskflow.dev", full_name="Charlie"
    )
    now = datetime.now(UTC)

    org = Organization(name="Beta Labs", slug="beta-labs")
    db_session.add(org)
    await db_session.flush()

    mem = OrganizationMembership(
        organization_id=org.id,
        user_id=uuid.UUID(user["id"]),
        role="member",
    )
    db_session.add(mem)

    project_a = Project(
        organization_id=org.id,
        name="Project Alpha",
        key="ALP",
        is_private=False,
    )
    project_b = Project(
        organization_id=org.id,
        name="Project Beta",
        key="BET",
        is_private=False,
    )
    db_session.add_all([project_a, project_b])
    await db_session.flush()

    # Create 4 tasks with various dates and statuses:
    # 1. Done item
    item_done = WorkItem(
        project_id=project_a.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="ALP-1",
        title="Completed setup",
        status="done",
        priority="low",
        assignee_id=uuid.UUID(user["id"]),
    )
    # 2. Overdue item
    item_overdue = WorkItem(
        project_id=project_a.id,
        organization_id=org.id,
        sequence_number=2,
        issue_key="ALP-2",
        title="Overdue bugfix",
        status="in_progress",
        priority="high",
        due_date=now - timedelta(days=3),
        assignee_id=uuid.UUID(user["id"]),
    )
    # 3. Upcoming item
    item_upcoming = WorkItem(
        project_id=project_a.id,
        organization_id=org.id,
        sequence_number=3,
        issue_key="ALP-3",
        title="Upcoming feature",
        status="todo",
        priority="urgent",
        due_date=now + timedelta(days=2),
        assignee_id=uuid.UUID(user["id"]),
    )
    # 4. Item without due date in Project Beta
    item_no_due = WorkItem(
        project_id=project_b.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="BET-1",
        title="Future backlog",
        status="todo",
        priority="medium",
        assignee_id=uuid.UUID(user["id"]),
    )
    db_session.add_all([item_done, item_overdue, item_upcoming, item_no_due])
    await db_session.commit()

    # 1. Test smart order (Default)
    # Order expected:
    # 1st: Overdue (ALP-2)
    # 2nd: Upcoming (ALP-3)
    # 3rd: Incomplete without due (BET-1)
    # 4th: Done (ALP-1)
    resp = await async_client.get(
        "/api/v1/dashboard/tasks",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["total"] == 4
    keys = [item["issue_key"] for item in res_data["items"]]
    assert keys == ["ALP-2", "ALP-3", "BET-1", "ALP-1"]
    assert res_data["items"][0]["is_overdue"] is True
    assert res_data["items"][1]["is_overdue"] is False

    # 2. Test status filter
    resp = await async_client.get(
        "/api/v1/dashboard/tasks?status=in_progress",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["total"] == 1
    assert res_data["items"][0]["issue_key"] == "ALP-2"

    # 3. Test project filter
    resp = await async_client.get(
        f"/api/v1/dashboard/tasks?project_id={project_b.id}",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["total"] == 1
    assert res_data["items"][0]["issue_key"] == "BET-1"

    # 4. Test pagination
    resp = await async_client.get(
        "/api/v1/dashboard/tasks?page=1&page_size=2",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["total"] == 4
    assert len(res_data["items"]) == 2
    assert res_data["page"] == 1
    assert res_data["total_pages"] == 2


@pytest.mark.asyncio
async def test_overdue_and_upcoming_tasks_endpoint(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Verify /tasks/overdue-upcoming groups overdue tasks and approaching tasks.
    """
    user, cookies, headers = await create_and_login_user(

        async_client, "david@taskflow.dev", full_name="David"
    )
    now = datetime.now(UTC)

    org = Organization(name="Delta Corp", slug="delta-corp")
    db_session.add(org)
    await db_session.flush()

    mem = OrganizationMembership(
        organization_id=org.id,
        user_id=uuid.UUID(user["id"]),
        role="member",
    )
    project = Project(
        organization_id=org.id,
        name="Delta Operations",
        key="DEL",
        is_private=False,
    )
    db_session.add_all([mem, project])
    await db_session.flush()

    # Overdue task
    t_overdue = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="DEL-1",
        title="Critical patch",
        status="in_progress",
        due_date=now - timedelta(days=1),
        assignee_id=uuid.UUID(user["id"]),
    )
    # Upcoming task (in 3 days, inside 7-day window)
    t_upcoming = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=2,
        issue_key="DEL-2",
        title="Sprint review prep",
        status="todo",
        due_date=now + timedelta(days=3),
        assignee_id=uuid.UUID(user["id"]),
    )
    # Far-future task (in 20 days, outside 7-day window)
    t_far = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=3,
        issue_key="DEL-3",
        title="Next quarter planning",
        status="todo",
        due_date=now + timedelta(days=20),
        assignee_id=uuid.UUID(user["id"]),
    )
    # Completed task that had a past due date (must NOT be in overdue)
    t_done_overdue = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=4,
        issue_key="DEL-4",
        title="Completed bug",
        status="done",
        due_date=now - timedelta(days=4),
        assignee_id=uuid.UUID(user["id"]),
    )
    db_session.add_all([t_overdue, t_upcoming, t_far, t_done_overdue])
    await db_session.commit()

    resp = await async_client.get(
        "/api/v1/dashboard/tasks/overdue-upcoming?upcoming_days=7",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()

    overdue_keys = [t["issue_key"] for t in data["overdue_tasks"]]
    upcoming_keys = [t["issue_key"] for t in data["upcoming_tasks"]]

    assert overdue_keys == ["DEL-1"]
    assert upcoming_keys == ["DEL-2"]


@pytest.mark.asyncio
async def test_recent_activity_stream(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """
    Verify real activity records are returned with actors, changes, and projects.
    """
    user_a, cookies_a, headers_a = await create_and_login_user(

        async_client, "emma@taskflow.dev", full_name="Emma"
    )
    user_b, _, _ = await create_and_login_user(
        async_client, "frank@taskflow.dev", full_name="Frank"
    )

    org = Organization(name="Echo Ltd", slug="echo-ltd")
    db_session.add(org)
    await db_session.flush()

    mem_a = OrganizationMembership(
        organization_id=org.id,
        user_id=uuid.UUID(user_a["id"]),
        role="member",
    )
    mem_b = OrganizationMembership(
        organization_id=org.id,
        user_id=uuid.UUID(user_b["id"]),
        role="member",
    )
    project = Project(
        organization_id=org.id,
        name="Echo System",
        key="ECH",
        is_private=False,
    )
    db_session.add_all([mem_a, mem_b, project])
    await db_session.flush()

    work_item = WorkItem(
        project_id=project.id,
        organization_id=org.id,
        sequence_number=1,
        issue_key="ECH-1",
        title="Audit trail implementation",
        status="in_progress",
        assignee_id=uuid.UUID(user_a["id"]),
    )
    db_session.add(work_item)
    await db_session.flush()

    # Create real WorkItemActivity event where Frank updated status on Emma's task
    act1 = WorkItemActivity(
        work_item_id=work_item.id,
        user_id=uuid.UUID(user_b["id"]),
        field="status",
        old_value="todo",
        new_value="in_progress",
    )
    # And another event where Emma updated priority
    act2 = WorkItemActivity(
        work_item_id=work_item.id,
        user_id=uuid.UUID(user_a["id"]),
        field="priority",
        old_value="medium",
        new_value="high",
    )
    db_session.add_all([act1, act2])
    await db_session.commit()

    resp = await async_client.get(
        "/api/v1/dashboard/activity",
        cookies=cookies_a,
        headers=headers_a,
    )
    assert resp.status_code == 200
    data = resp.json()

    assert data["total"] == 2
    items = data["items"]
    # Check that actor info, work item key, and details are accurately populated
    assert items[0]["work_item_key"] == "ECH-1"
    assert items[0]["project_name"] == "Echo System"
    assert items[0]["actor"]["full_name"] in ["Emma", "Frank"]


@pytest.mark.asyncio
async def test_tenant_and_cross_organization_isolation(
    async_client: AsyncClient,
    db_session: AsyncSession,
):
    """Ensure user from Org 1 cannot see any projects, tasks, or activities of Org 2."""
    user_1, cookies_1, headers_1 = await create_and_login_user(
        async_client, "org1_user@taskflow.dev", full_name="Org 1 User"
    )
    user_2, cookies_2, headers_2 = await create_and_login_user(
        async_client, "org2_user@taskflow.dev", full_name="Org 2 User"
    )

    org1 = Organization(name="Tenant One", slug="tenant-one")
    org2 = Organization(name="Tenant Two", slug="tenant-two")
    db_session.add_all([org1, org2])
    await db_session.flush()

    mem1 = OrganizationMembership(
        organization_id=org1.id,
        user_id=uuid.UUID(user_1["id"]),
        role="member",
    )
    mem2 = OrganizationMembership(
        organization_id=org2.id,
        user_id=uuid.UUID(user_2["id"]),
        role="member",
    )
    db_session.add_all([mem1, mem2])
    await db_session.flush()

    proj1 = Project(
        organization_id=org1.id,
        name="Tenant One Project",
        key="T1",
        is_private=False,
    )
    proj2 = Project(
        organization_id=org2.id,
        name="Tenant Two Project",
        key="T2",
        is_private=False,
    )
    db_session.add_all([proj1, proj2])
    await db_session.flush()

    # Task in Org 2 assigned to user 2
    task2 = WorkItem(
        project_id=proj2.id,
        organization_id=org2.id,
        sequence_number=1,
        issue_key="T2-1",
        title="Confidential Org 2 Task",
        status="in_progress",
        assignee_id=uuid.UUID(user_2["id"]),
    )
    db_session.add(task2)
    await db_session.flush()

    act2 = WorkItemActivity(
        work_item_id=task2.id,
        user_id=uuid.UUID(user_2["id"]),
        field="status",
        old_value="todo",
        new_value="in_progress",
    )
    db_session.add(act2)
    await db_session.commit()

    # User 1 queries dashboard
    sum_resp = await async_client.get(
        "/api/v1/dashboard/summary", cookies=cookies_1, headers=headers_1
    )
    assert sum_resp.status_code == 200
    sum_data = sum_resp.json()
    assert sum_data["total_projects"] == 1
    assert sum_data["projects"][0]["key"] == "T1"

    # User 1 queries tasks
    tasks_resp = await async_client.get(
        "/api/v1/dashboard/tasks", cookies=cookies_1, headers=headers_1
    )
    assert tasks_resp.status_code == 200
    assert tasks_resp.json()["total"] == 0

    # User 1 queries activity
    act_resp = await async_client.get(
        "/api/v1/dashboard/activity", cookies=cookies_1, headers=headers_1
    )
    assert act_resp.status_code == 200
    assert act_resp.json()["total"] == 0
