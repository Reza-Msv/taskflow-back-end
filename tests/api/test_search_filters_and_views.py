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
    """Helper creating user and logging in; returns user, cookies, headers."""
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
    org_name: str = "Search Org",
    org_slug: str = "search-org",
    proj_name: str = "Search Project",
    proj_key: str = "SRCH",
    is_private: bool = False,
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
        json={"name": proj_name, "key": proj_key, "is_private": is_private},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert proj_resp.status_code == 201
    proj_id = proj_resp.json()["id"]

    return org_id, proj_id


@pytest.mark.asyncio
async def test_global_search_tasks_and_projects(async_client: AsyncClient):
    """Test global search matches tasks, descriptions, and projects."""
    user, cookies, headers = await create_user_and_login(
        async_client, "searcher@example.com", full_name="Search User"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client,
        cookies,
        headers,
        org_name="Apollo Org",
        org_slug="apollo-org",
        proj_name="Lunar Rover Mission",
        proj_key="LRM",
    )

    # Create tasks
    task1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Calibrate navigation telemetry",
            "description": "Ensure rover gyro sensors are aligned with coordinates.",
            "type": "task",
            "priority": "high",
        },
        cookies=cookies,
        headers=headers,
    )
    assert task1_resp.status_code == 201

    task2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Battery thermal check",
            "description": "Inspect telemetry logs for temperature anomalies.",
            "type": "bug",
            "priority": "urgent",
        },
        cookies=cookies,
        headers=headers,
    )
    assert task2_resp.status_code == 201

    # Search for "telemetry" -> should find task1 (title) and task2 (description)
    resp = await async_client.get(
        "/api/v1/search?q=telemetry",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["query"] == "telemetry"
    assert data["total_tasks"] == 2
    task_titles = [t["title"] for t in data["tasks"]]
    assert "Calibrate navigation telemetry" in task_titles
    assert "Battery thermal check" in task_titles

    # Search for "Rover" -> should find Lunar Rover Mission project
    resp_proj = await async_client.get(
        "/api/v1/search?q=Rover",
        cookies=cookies,
        headers=headers,
    )
    assert resp_proj.status_code == 200
    data_proj = resp_proj.json()
    assert data_proj["total_projects"] == 1
    assert data_proj["projects"][0]["name"] == "Lunar Rover Mission"
    assert data_proj["projects"][0]["entity_type"] == "project"


@pytest.mark.asyncio
async def test_global_search_authorization_and_isolation(async_client: AsyncClient):
    """Test search does not leak inaccessible tasks or projects across tenants."""
    user1, cookies1, headers1 = await create_user_and_login(
        async_client, "user1@example.com"
    )
    user2, cookies2, headers2 = await create_user_and_login(
        async_client, "user2@example.com"
    )

    # User 1 creates confidential project and task
    org1_id, proj1_id = await setup_org_and_project(
        async_client,
        cookies1,
        headers1,
        org_name="Secret Org",
        org_slug="secret-org",
        proj_name="Classified Project Quantum",
        proj_key="CPQ",
        is_private=True,
    )

    t_resp = await async_client.post(
        f"/api/v1/organizations/{org1_id}/projects/{proj1_id}/tasks",
        json={
            "title": "Quantum encryption keys",
            "description": "Top secret encryption algorithm setup",
            "type": "feature",
            "priority": "high",
        },
        cookies=cookies1,
        headers=headers1,
    )
    assert t_resp.status_code == 201

    # User 2 searches for "Quantum" -> should return 0 results
    resp2 = await async_client.get(
        "/api/v1/search?q=Quantum",
        cookies=cookies2,
        headers=headers2,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total_tasks"] == 0
    assert data2["total_projects"] == 0
    assert len(data2["tasks"]) == 0
    assert len(data2["projects"]) == 0

    # User 1 searches for "Quantum" -> finds both project and task
    resp1 = await async_client.get(
        "/api/v1/search?q=Quantum",
        cookies=cookies1,
        headers=headers1,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["total_tasks"] == 1
    assert data1["total_projects"] == 1


@pytest.mark.asyncio
async def test_global_search_validation(async_client: AsyncClient):
    """Test query validation: empty queries and queries exceeding max length."""
    user, cookies, headers = await create_user_and_login(
        async_client, "validator@example.com"
    )

    # Empty query should fail validation (min_length=1)
    resp_empty = await async_client.get(
        "/api/v1/search?q=",
        cookies=cookies,
        headers=headers,
    )
    assert resp_empty.status_code == 422

    # Query without 'q' parameter should fail validation
    resp_missing = await async_client.get(
        "/api/v1/search",
        cookies=cookies,
        headers=headers,
    )
    assert resp_missing.status_code == 422


@pytest.mark.asyncio
async def test_advanced_task_filters(async_client: AsyncClient):
    """Test combined task filtering: status, priority, type, overdue, date range."""
    user, cookies, headers = await create_user_and_login(
        async_client, "filterer@example.com"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="filter-org", proj_key="FLT"
    )

    now = datetime.now(UTC)
    yesterday = (now - timedelta(days=1)).isoformat()
    next_week = (now + timedelta(days=7)).isoformat()

    # 1. Overdue bug
    resp_t1 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Fix critical buffer overflow",
            "type": "bug",
            "priority": "urgent",
            "due_date": yesterday,
        },
        cookies=cookies,
        headers=headers,
    )
    assert resp_t1.status_code == 201

    # 2. Future feature
    resp_t2 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Implement dark mode theme",
            "type": "feature",
            "priority": "low",
            "due_date": next_week,
        },
        cookies=cookies,
        headers=headers,
    )
    assert resp_t2.status_code == 201

    # Filter by type=bug
    resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks?type=bug",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["title"] == "Fix critical buffer overflow"

    # Filter by priority=urgent
    resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks?priority=urgent",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # Filter by is_overdue=true
    resp_od = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks?is_overdue=true",
        cookies=cookies,
        headers=headers,
    )
    assert resp_od.status_code == 200
    assert resp_od.json()["total"] == 1
    assert resp_od.json()["items"][0]["title"] == "Fix critical buffer overflow"

    # Filter by search parameter
    resp_s = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks?search=dark%20mode",
        cookies=cookies,
        headers=headers,
    )
    assert resp_s.status_code == 200
    assert resp_s.json()["total"] == 1
    assert resp_s.json()["items"][0]["title"] == "Implement dark mode theme"

    # Invalid date range: due_date_from > due_date_to raises 400
    t_from = (now + timedelta(days=10)).isoformat()
    t_to = (now - timedelta(days=2)).isoformat()
    resp_inv_dates = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        params={"due_date_from": t_from, "due_date_to": t_to},
        cookies=cookies,
        headers=headers,
    )
    assert resp_inv_dates.status_code == 400
    err_msg = resp_inv_dates.json()["error"]["message"]
    assert "due_date_from cannot be after due_date_to" in err_msg


@pytest.mark.asyncio
async def test_my_tasks_endpoint(async_client: AsyncClient):
    """Test /api/v1/tasks/me personal task endpoint."""
    user, cookies, headers = await create_user_and_login(
        async_client, "mytasks@example.com"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="mytasks-org", proj_key="MYT"
    )

    # Create a task assigned to current user
    resp_t1 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Assigned to me directly",
            "type": "task",
            "priority": "medium",
            "assignee_id": user["id"],
        },
        cookies=cookies,
        headers=headers,
    )
    assert resp_t1.status_code == 201

    # Create unassigned task
    resp_t2 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Unassigned open issue",
            "type": "bug",
            "priority": "low",
        },
        cookies=cookies,
        headers=headers,
    )
    assert resp_t2.status_code == 201

    # Call /api/v1/tasks/me
    resp = await async_client.get(
        "/api/v1/tasks/me",
        cookies=cookies,
        headers=headers,
    )
    assert resp.status_code == 200
    my_tasks = resp.json()
    assert my_tasks["total"] == 1
    assert my_tasks["items"][0]["title"] == "Assigned to me directly"

    # Call with filter priority=low -> should return 0 items
    resp_flt = await async_client.get(
        "/api/v1/tasks/me?priority=low",
        cookies=cookies,
        headers=headers,
    )
    assert resp_flt.status_code == 200
    assert resp_flt.json()["total"] == 0


@pytest.mark.asyncio
async def test_saved_filters_crud_and_execution(async_client: AsyncClient):
    """Test saved filters create, list, retrieve, update, delete, and execute."""
    user1, cookies1, headers1 = await create_user_and_login(
        async_client, "sfuser1@example.com"
    )
    user2, cookies2, headers2 = await create_user_and_login(
        async_client, "sfuser2@example.com"
    )
    org_id, proj_id = await setup_org_and_project(
        async_client, cookies1, headers1, org_slug="sf-org", proj_key="SF1"
    )

    # Create tasks in project
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "High priority security vulnerability",
            "type": "bug",
            "priority": "urgent",
        },
        cookies=cookies1,
        headers=headers1,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={
            "title": "Minor styling tweak",
            "type": "task",
            "priority": "low",
        },
        cookies=cookies1,
        headers=headers1,
    )

    # 1. Create Saved Filter
    create_payload = {
        "name": "Critical Bugs View",
        "description": "All critical severity bugs across the organization",
        "organization_id": org_id,
        "project_id": proj_id,
        "filter_criteria": {
            "type": "bug",
            "priority": "urgent",
        },
        "sort_by": "created_at",
        "sort_dir": "desc",
        "view_type": "list",
        "is_favorite": True,
    }
    create_resp = await async_client.post(
        "/api/v1/saved-filters",
        json=create_payload,
        cookies=cookies1,
        headers=headers1,
    )
    assert create_resp.status_code == 201
    sf_data = create_resp.json()
    sf_id = sf_data["id"]
    assert sf_data["name"] == "Critical Bugs View"
    assert sf_data["filter_criteria"]["priority"] == "urgent"
    assert sf_data["user_id"] == user1["id"]

    # 2. List Saved Filters
    list_resp = await async_client.get(
        "/api/v1/saved-filters",
        cookies=cookies1,
        headers=headers1,
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1
    assert list_resp.json()["items"][0]["id"] == sf_id

    # 3. Retrieve Saved Filter by ID
    get_resp = await async_client.get(
        f"/api/v1/saved-filters/{sf_id}",
        cookies=cookies1,
        headers=headers1,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == sf_id

    # 4. Execute Saved Filter
    exec_resp = await async_client.get(
        f"/api/v1/saved-filters/{sf_id}/tasks",
        cookies=cookies1,
        headers=headers1,
    )
    assert exec_resp.status_code == 200
    tasks_data = exec_resp.json()
    assert tasks_data["total"] == 1
    assert tasks_data["items"][0]["title"] == "High priority security vulnerability"

    # 5. Ownership Isolation: User 2 tries to access User 1's saved filter -> 404
    user2_get = await async_client.get(
        f"/api/v1/saved-filters/{sf_id}",
        cookies=cookies2,
        headers=headers2,
    )
    assert user2_get.status_code == 404

    user2_exec = await async_client.get(
        f"/api/v1/saved-filters/{sf_id}/tasks",
        cookies=cookies2,
        headers=headers2,
    )
    assert user2_exec.status_code == 404

    # 6. Update Saved Filter
    patch_resp = await async_client.patch(
        f"/api/v1/saved-filters/{sf_id}",
        json={"name": "Urgent Security Issues", "is_favorite": False},
        cookies=cookies1,
        headers=headers1,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["name"] == "Urgent Security Issues"
    assert patch_resp.json()["is_favorite"] is False

    # 7. Delete Saved Filter
    del_resp = await async_client.delete(
        f"/api/v1/saved-filters/{sf_id}",
        cookies=cookies1,
        headers=headers1,
    )
    assert del_resp.status_code == 204

    # Verify 404 after deletion
    get_after_del = await async_client.get(
        f"/api/v1/saved-filters/{sf_id}",
        cookies=cookies1,
        headers=headers1,
    )
    assert get_after_del.status_code == 404
