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
    org_name: str = "Reports Org",
    org_slug: str = "reports-org",
    proj_name: str = "Reports Project",
    proj_key: str = "REP",
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
# 1. Project Overview Report Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_project_overview_metrics_and_zero_denominator(
    async_client: AsyncClient,
):
    """Verify overview report aggregates and zero-division handling."""
    user, cookies, headers = await create_user_and_login(
        async_client, email="rep_overview@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="rep-ov-org", proj_key="ROV"
    )

    # 1. Empty project: should return 0 counts and 0.0% completion
    empty_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/overview",
        cookies=cookies,
        headers=headers,
    )
    assert empty_resp.status_code == 200, empty_resp.text
    empty_data = empty_resp.json()
    assert empty_data["total_tasks"] == 0
    assert empty_data["completed_tasks"] == 0
    assert empty_data["open_tasks"] == 0
    assert empty_data["in_progress_tasks"] == 0
    assert empty_data["overdue_tasks"] == 0
    assert empty_data["unassigned_tasks"] == 0
    assert empty_data["completion_percentage"] == 0.0
    assert empty_data["total_estimated_points"] == 0.0

    # 2. Add tasks with various statuses, assignees, dates, and story points
    now = datetime.now(UTC)

    # Task 1: Completed task with 3 points
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Completed Task",
            "status": "done",
            "estimated_points": 3.0,
        },
        cookies=cookies,
        headers=headers,
    )
    assert t1_resp.status_code == 201

    # Task 2: In-progress task with 5 points, assigned to user
    t2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "In Progress Task",
            "status": "in_progress",
            "assignee_id": user["id"],
            "estimated_points": 5.0,
        },
        cookies=cookies,
        headers=headers,
    )
    assert t2_resp.status_code == 201

    # Task 3: Open task, unassigned, due soon (in 3 days)
    t3_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Due Soon Task",
            "status": "todo",
            "due_date": (now + timedelta(days=3)).isoformat(),
            "estimated_points": 2.0,
        },
        cookies=cookies,
        headers=headers,
    )
    assert t3_resp.status_code == 201

    # Task 4: Open task, overdue (due 2 days ago), assigned to user
    t4_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Overdue Task",
            "status": "todo",
            "assignee_id": user["id"],
            "due_date": (now - timedelta(days=2)).isoformat(),
            "estimated_points": 1.0,
        },
        cookies=cookies,
        headers=headers,
    )
    assert t4_resp.status_code == 201

    # 3. Request overview report with default 7-day window
    ov_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/overview",
        params={"due_window_days": 7},
        cookies=cookies,
        headers=headers,
    )
    assert ov_resp.status_code == 200
    ov = ov_resp.json()

    assert ov["total_tasks"] == 4
    assert ov["completed_tasks"] == 1
    assert ov["in_progress_tasks"] == 1
    assert ov["open_tasks"] == 2
    assert ov["overdue_tasks"] == 1
    assert ov["unassigned_tasks"] == 1
    assert ov["due_soon_tasks"] == 1
    assert ov["completion_percentage"] == 25.0
    assert ov["total_estimated_points"] == 11.0
    assert ov["completed_estimated_points"] == 3.0

    # Verify distributions
    assert len(ov["status_distribution"]) >= 3
    assert len(ov["priority_distribution"]) >= 1
    assert len(ov["type_distribution"]) >= 1


# -------------------------------------------------------------
# 2. Completion Trends Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_completion_trends_intervals_and_empty_buckets(
    async_client: AsyncClient,
):
    """Verify completion trends intervals, bucket continuity, and validations."""
    _, cookies, headers = await create_user_and_login(
        async_client, email="rep_trends@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="rep-tr-org", proj_key="RTR"
    )

    base = datetime.now(UTC)
    start_date = base - timedelta(days=5)
    end_date = base + timedelta(days=1)

    # 1. Create and complete tasks
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Task To Complete 1", "estimated_points": 2.0},
        cookies=cookies,
        headers=headers,
    )
    t1_id = t1_resp.json()["id"]

    # Transition to done to set completed_at
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{t1_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )

    # 2. Daily interval query
    trends_day = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/completion-trends",
        params={
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "interval": "day",
        },
        cookies=cookies,
        headers=headers,
    )
    assert trends_day.status_code == 200, trends_day.text
    data_day = trends_day.json()
    assert data_day["total_completed_in_period"] == 1
    assert data_day["total_points_completed_in_period"] == 2.0
    assert len(data_day["buckets"]) >= 6  # 5 days ago to tomorrow
    # Empty intervals have 0 completed tasks
    empty_buckets = [b for b in data_day["buckets"] if b["completed_tasks"] == 0]
    assert len(empty_buckets) > 0

    # 3. Weekly interval query
    trends_week = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/completion-trends",
        params={
            "start_date": (base - timedelta(days=21)).isoformat(),
            "end_date": end_date.isoformat(),
            "interval": "week",
        },
        cookies=cookies,
        headers=headers,
    )
    assert trends_week.status_code == 200
    data_week = trends_week.json()
    assert len(data_week["buckets"]) >= 3

    # 4. Monthly interval query
    trends_month = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/completion-trends",
        params={
            "start_date": (base - timedelta(days=60)).isoformat(),
            "end_date": end_date.isoformat(),
            "interval": "month",
        },
        cookies=cookies,
        headers=headers,
    )
    assert trends_month.status_code == 200
    data_month = trends_month.json()
    assert len(data_month["buckets"]) >= 2

    # 5. Invalid date range (end_date < start_date) -> 400
    invalid_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/completion-trends",
        params={
            "start_date": end_date.isoformat(),
            "end_date": start_date.isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert invalid_resp.status_code == 400
    assert invalid_resp.json()["error"]["code"] == "INVALID_DATE_RANGE"

    # 6. Exceeding 366 days -> 400
    too_large = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/completion-trends",
        params={
            "start_date": (base - timedelta(days=400)).isoformat(),
            "end_date": base.isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert too_large.status_code == 400
    assert too_large.json()["error"]["code"] == "DATE_RANGE_TOO_LARGE"


# -------------------------------------------------------------
# 3. Team Workload Report Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_team_workload_and_unassigned_work(
    async_client: AsyncClient,
):
    """Verify team workload distribution, unassigned work, and member privacy."""
    owner, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="rep_wl_owner@example.com", full_name="Workload Owner"
    )
    org_id, project_id = await setup_org_and_project(
        async_client,
        owner_cookies,
        owner_headers,
        org_slug="rep-wl-org",
        proj_key="RWL",
    )

    # 1. Create a second team member
    member, member_cookies, member_headers = await create_user_and_login(
        async_client, email="rep_wl_member@example.com", full_name="Team Member"
    )

    # Add member to org
    add_org = await async_client.post(
        f"/api/v1/organizations/{org_id}/members",
        json={"user_id": member["id"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert add_org.status_code == 201

    # Add member to project
    add_proj = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/members",
        json={"user_id": member["id"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert add_proj.status_code == 201

    now = datetime.now(UTC)

    # 2. Assign tasks:
    # Owner tasks: 1 in-progress (3 pts), 1 overdue (2 pts)
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Owner In Progress",
            "status": "in_progress",
            "assignee_id": owner["id"],
            "estimated_points": 3.0,
        },
        cookies=owner_cookies,
        headers=owner_headers,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Owner Overdue",
            "status": "todo",
            "assignee_id": owner["id"],
            "due_date": (now - timedelta(days=2)).isoformat(),
            "estimated_points": 2.0,
        },
        cookies=owner_cookies,
        headers=owner_headers,
    )

    # Member task: 1 in-progress (5 pts)
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Member Task",
            "status": "in_progress",
            "assignee_id": member["id"],
            "estimated_points": 5.0,
        },
        cookies=owner_cookies,
        headers=owner_headers,
    )

    # Unassigned tasks: 1 in-progress (1 pt), 1 todo (4 pts)
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Unassigned In Progress",
            "status": "in_progress",
            "estimated_points": 1.0,
        },
        cookies=owner_cookies,
        headers=owner_headers,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Unassigned Todo",
            "status": "todo",
            "estimated_points": 4.0,
        },
        cookies=owner_cookies,
        headers=owner_headers,
    )

    # 3. Fetch workload report
    wl_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/team-workload",
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert wl_resp.status_code == 200, wl_resp.text
    wl = wl_resp.json()

    assert wl["total_members"] == 2
    owner_wl = next((m for m in wl["members"] if m["user"]["id"] == owner["id"]), None)
    assert owner_wl is not None
    assert owner_wl["assigned_tasks"] == 2
    assert owner_wl["in_progress_tasks"] == 1
    assert owner_wl["overdue_tasks"] == 1
    assert owner_wl["total_story_points"] == 5.0

    member_wl = next(
        (m for m in wl["members"] if m["user"]["id"] == member["id"]), None
    )
    assert member_wl is not None
    assert member_wl["assigned_tasks"] == 1
    assert member_wl["in_progress_tasks"] == 1
    assert member_wl["total_story_points"] == 5.0

    # Check unassigned work
    assert wl["unassigned_work"]["unassigned_tasks"] == 2
    assert wl["unassigned_work"]["unassigned_in_progress_tasks"] == 1
    assert wl["unassigned_work"]["unassigned_story_points"] == 5.0


# -------------------------------------------------------------
# 4. Sprint Analytics Report Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_sprint_analytics_and_burndown(
    async_client: AsyncClient,
):
    """Verify sprint analytics integration with summary, velocity, and burndown."""
    _, cookies, headers = await create_user_and_login(
        async_client, email="rep_sprint@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="rep-sp-org", proj_key="RSP"
    )

    base = datetime.now(UTC)

    # 1. Create a sprint
    sp_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/sprints",
        json={
            "name": "Sprint 1 Reports",
            "start_date": base.isoformat(),
            "end_date": (base + timedelta(days=14)).isoformat(),
        },
        cookies=cookies,
        headers=headers,
    )
    assert sp_resp.status_code == 201
    sprint_id = sp_resp.json()["id"]

    # 2. Add tasks to sprint
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Sprint Task 1",
            "sprint_id": sprint_id,
            "estimated_points": 5.0,
        },
        cookies=cookies,
        headers=headers,
    )
    t1_id = t1_resp.json()["id"]

    # 3. Start sprint
    start_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/sprints/{sprint_id}/start",
        json={},
        cookies=cookies,
        headers=headers,
    )
    assert start_resp.status_code == 200

    # 4. Complete task
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{t1_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )

    # 5. Fetch single sprint analytics report
    sp_report = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/sprints/{sprint_id}",
        cookies=cookies,
        headers=headers,
    )
    assert sp_report.status_code == 200, sp_report.text
    rep_data = sp_report.json()
    assert rep_data["sprint_id"] == sprint_id
    assert rep_data["sprint_name"] == "Sprint 1 Reports"
    assert rep_data["committed_points"] == 5.0
    assert rep_data["completed_points"] == 5.0
    assert rep_data["completion_percentage"] == 100.0
    assert len(rep_data["burndown"]) > 0

    # 6. Fetch project-wide sprint analytics (velocity history)
    proj_sp_report = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/sprints",
        cookies=cookies,
        headers=headers,
    )
    assert proj_sp_report.status_code == 200
    assert "average_velocity" in proj_sp_report.json()


# -------------------------------------------------------------
# 5. CSV Export & Formula Injection Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_csv_export_authorization_and_formula_injection(
    async_client: AsyncClient,
):
    """Verify CSV export formatting, UTF-8 BOM, and formula-injection sanitization."""
    user, cookies, headers = await create_user_and_login(
        async_client,
        email="rep_csv@example.com",
        full_name="=cmd|' /C calc'!A0",  # Potential formula injection in full name
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="rep-csv-org", proj_key="RCSV"
    )

    # Create task with formula-like title: =1+1
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "=1+1 Formula Task", "assignee_id": user["id"]},
        cookies=cookies,
        headers=headers,
    )

    # 1. Export Overview CSV
    ov_csv_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/export",
        params={"report_type": "overview"},
        cookies=cookies,
        headers=headers,
    )
    assert ov_csv_resp.status_code == 200
    assert "text/csv" in ov_csv_resp.headers["content-type"]
    assert "rcsv_overview_report.csv" in ov_csv_resp.headers["content-disposition"]
    content = ov_csv_resp.text
    # Verify UTF-8 BOM is present at the beginning
    assert content.startswith("\ufeff")
    assert "Project Overview Report" in content
    assert "Total Tasks,1" in content

    # 2. Export Team Workload CSV and check formula injection sanitization
    wl_csv_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/export",
        params={"report_type": "team_workload"},
        cookies=cookies,
        headers=headers,
    )
    assert wl_csv_resp.status_code == 200
    wl_content = wl_csv_resp.text
    # Full name starts with '=', must be escaped with single quote
    assert "'=cmd|" in wl_content

    # 3. Export Completion Trends CSV
    trends_csv = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/export",
        params={"report_type": "completion_trends"},
        cookies=cookies,
        headers=headers,
    )
    assert trends_csv.status_code == 200
    assert "Completion Trends Report" in trends_csv.text

    # 4. Export Sprint Summary CSV
    sprint_csv = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/reports/export",
        params={"report_type": "sprint_summary"},
        cookies=cookies,
        headers=headers,
    )
    assert sprint_csv.status_code == 200
    assert "Sprint Analytics Report" in sprint_csv.text


# -------------------------------------------------------------
# 6. Cross-Project & RBAC Access Tests
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_project_and_tenant_isolation(
    async_client: AsyncClient,
):
    """Verify 403/404 on cross-org, cross-project, and viewer access."""
    # 1. Setup Owner 1 with Project 1
    _, cookies1, headers1 = await create_user_and_login(
        async_client, email="iso_owner1@example.com"
    )
    org1_id, proj1_id = await setup_org_and_project(
        async_client, cookies1, headers1, org_slug="iso-org-1", proj_key="ISO1"
    )

    # 2. Setup Owner 2 with Project 2
    _, cookies2, headers2 = await create_user_and_login(
        async_client, email="iso_owner2@example.com"
    )
    org2_id, proj2_id = await setup_org_and_project(
        async_client, cookies2, headers2, org_slug="iso-org-2", proj_key="ISO2"
    )

    # 3. User 2 attempts to access Project 1 reports -> 403 Forbidden
    forbidden_resp = await async_client.get(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/reports/overview",
        cookies=cookies2,
        headers=headers2,
    )
    assert forbidden_resp.status_code == 403

    # 4. Cross-tenant mismatch (Org 2 with Project 1) -> 404 Not Found
    mismatch_resp = await async_client.get(
        f"/api/v1/organizations/{org2_id}/projects/{proj1_id}/reports/overview",
        cookies=cookies2,
        headers=headers2,
    )
    assert mismatch_resp.status_code == 404

    # 5. Viewer user in Project 1 CAN view reports (read-only)
    viewer, viewer_cookies, viewer_headers = await create_user_and_login(
        async_client, email="iso_viewer_rep@example.com"
    )
    # Add to org as member
    await async_client.post(
        f"/api/v1/organizations/{org1_id}/members",
        json={"user_id": viewer["id"], "role": "member"},
        cookies=cookies1,
        headers=headers1,
    )
    # Add to project as viewer
    await async_client.post(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/members",
        json={"user_id": viewer["id"], "role": "viewer"},
        cookies=cookies1,
        headers=headers1,
    )

    # Viewer can access overview and workload
    v_overview = await async_client.get(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/reports/overview",
        cookies=viewer_cookies,
        headers=viewer_headers,
    )
    assert v_overview.status_code == 200

    v_workload = await async_client.get(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/reports/team-workload",
        cookies=viewer_cookies,
        headers=viewer_headers,
    )
    assert v_workload.status_code == 200
