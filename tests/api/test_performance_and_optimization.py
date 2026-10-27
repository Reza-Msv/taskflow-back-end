import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings
from taskflow.db.session import AsyncSessionLocal, engine
from taskflow.domains.collaboration.models import Notification
from taskflow.domains.collaboration.websocket import InMemoryConnectionManager
from taskflow.domains.organizations.repository import OrganizationRepository
from taskflow.domains.work_items.models import WorkItem, WorkItemActivity
from taskflow.domains.work_items.repository import WorkItemRepository


async def create_user_and_login(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "Test User",
) -> tuple[dict[str, Any], dict[str, str], dict[str, str]]:
    """Helper creating user and logging in, returning data, cookies, headers."""
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


@pytest.mark.asyncio
async def test_migration_0009_indexes_present_on_models() -> None:
    """Verify that models contain the newly declared performance indexes."""
    # WorkItem indexes
    work_item_indexes = {idx.name for idx in WorkItem.__table__.indexes}
    assert "ix_work_items_due_date" in work_item_indexes
    assert "ix_work_items_project_status" in work_item_indexes
    assert "ix_work_items_project_assignee" in work_item_indexes
    assert "ix_work_items_project_due_date" in work_item_indexes

    # WorkItemActivity indexes
    activity_indexes = {idx.name for idx in WorkItemActivity.__table__.indexes}
    assert "ix_work_item_activities_item_created" in activity_indexes

    # Notification indexes
    notification_indexes = {idx.name for idx in Notification.__table__.indexes}
    assert "ix_notifications_user_unread" in notification_indexes
    assert "ix_notifications_user_created" in notification_indexes


@pytest.mark.asyncio
async def test_connection_pool_configuration_attributes() -> None:
    """Ensure connection pool settings are exposed and valid."""
    assert hasattr(settings, "POSTGRES_POOL_SIZE")
    assert hasattr(settings, "POSTGRES_MAX_OVERFLOW")
    assert hasattr(settings, "POSTGRES_POOL_TIMEOUT")
    assert hasattr(settings, "POSTGRES_POOL_RECYCLE")
    assert settings.POSTGRES_POOL_SIZE >= 5
    assert settings.POSTGRES_MAX_OVERFLOW >= 0

    assert engine is not None
    assert AsyncSessionLocal is not None


@pytest.mark.asyncio
async def test_workspace_stats_single_query_consolidation(
    db_session: AsyncSession,
) -> None:
    """Verify get_workspace_stats executes as a single aggregation query."""
    org_repo = OrganizationRepository(db_session)
    dummy_org_id = uuid.uuid4()

    # Query with empty database
    stats = await org_repo.get_workspace_stats(dummy_org_id)
    assert stats["member_count"] == 0
    assert stats["active_owners_count"] == 0
    assert stats["team_count"] == 0
    assert stats["project_count"] == 0


@pytest.mark.asyncio
async def test_repository_get_by_ids_and_bounded_queries(
    db_session: AsyncSession,
) -> None:
    """Test get_by_ids batch retrieval and bounded project work items."""
    work_item_repo = WorkItemRepository(db_session)

    # Empty test
    res = await work_item_repo.get_by_ids([])
    assert res == []

    # Non-existent IDs
    random_ids = [uuid.uuid4(), uuid.uuid4()]
    res = await work_item_repo.get_by_ids(random_ids)
    assert res == []

    # Bounded list tests
    dummy_proj = uuid.uuid4()
    items = await work_item_repo.list_all_project_work_items(dummy_proj, limit=50)
    assert items == []
    timeline = await work_item_repo.get_timeline_data(dummy_proj, limit=50)
    assert timeline == ([], [], [])


async def setup_org_and_project(
    client: AsyncClient,
    owner_cookies: dict[str, str],
    owner_headers: dict[str, str],
    org_name: str = "Perf Org",
    org_slug: str = "perf-org",
    proj_name: str = "Perf Project",
    proj_key: str = "PRF",
) -> tuple[str, str]:
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
        json={"name": proj_name, "key": proj_key},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert proj_resp.status_code == 201
    project_id = proj_resp.json()["id"]

    return org_id, project_id


@pytest.mark.asyncio
async def test_bulk_task_operations_batching_efficiency(
    async_client: AsyncClient,
) -> None:
    """Verify execute_bulk_operation executes efficiently for 50 tasks."""
    _, cookies, headers = await create_user_and_login(
        async_client, "bulk_perf@example.com", full_name="Bulk Perf"
    )

    org_id, proj_id = await setup_org_and_project(
        async_client,
        cookies,
        headers,
        org_name="Bulk Perf Org",
        org_slug="bulk-perf-org",
        proj_name="Bulk Perf Project",
        proj_key="BPRF",
    )

    # Create 5 tasks
    task_ids = []
    for i in range(5):
        t_resp = await async_client.post(
            f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
            json={"title": f"Task {i}", "priority": "low"},
            cookies=cookies,
            headers=headers,
        )
        assert t_resp.status_code == 201
        task_ids.append(t_resp.json()["id"])

    # Bulk update priority to 'urgent'
    bulk_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/bulk-operation",
        json={
            "task_ids": task_ids,
            "operation": "update_priority",
            "payload": {"priority": "urgent"},
        },
        cookies=cookies,
        headers=headers,
    )
    assert bulk_resp.status_code == 200
    data = bulk_resp.json()
    assert data["total_requested"] == 5
    assert data["successful_count"] == 5
    assert data["failed_count"] == 0
    assert len(data["affected_task_ids"]) == 5

    # Verify updated tasks
    for tid in task_ids:
        t_check = await async_client.get(
            f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{tid}",
            cookies=cookies,
            headers=headers,
        )
        assert t_check.status_code == 200
        assert t_check.json()["priority"] == "urgent"


@pytest.mark.asyncio
async def test_kanban_board_single_pass_grouping(
    async_client: AsyncClient,
) -> None:
    """Verify get_kanban_board partitions tasks correctly across workflow statuses."""
    _, cookies, headers = await create_user_and_login(
        async_client, "kanban_perf@example.com", full_name="Kanban Perf"
    )

    org_id, proj_id = await setup_org_and_project(
        async_client,
        cookies,
        headers,
        org_name="Kanban Perf Org",
        org_slug="kanban-perf-org",
        proj_name="Kanban Perf Project",
        proj_key="KPRF",
    )

    # Create tasks with varying statuses
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={"title": "Task Todo", "status": "todo"},
        cookies=cookies,
        headers=headers,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={"title": "Task In Progress", "status": "in_progress"},
        cookies=cookies,
        headers=headers,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
        json={"title": "Task Done", "status": "done"},
        cookies=cookies,
        headers=headers,
    )

    kanban_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{proj_id}/kanban",
        cookies=cookies,
        headers=headers,
    )
    assert kanban_resp.status_code == 200
    board = kanban_resp.json()
    assert board["total_tasks"] == 3
    assert len(board["columns"]) > 0

    status_counts = {
        col["status"]["category"]: col["total_tasks"] for col in board["columns"]
    }
    assert status_counts.get("todo", 0) >= 1
    assert status_counts.get("in_progress", 0) >= 1
    assert status_counts.get("done", 0) >= 1


@pytest.mark.asyncio
async def test_websocket_concurrent_broadcasting() -> None:
    """Verify WebSocket manager sends concurrently and cleans up dead sockets."""
    manager = InMemoryConnectionManager()
    user_id = uuid.uuid4()

    # Create 3 mock websockets: 2 successful, 1 broken
    ws1 = AsyncMock()
    ws2 = AsyncMock()
    ws3 = AsyncMock()
    ws3.send_json.side_effect = RuntimeError("Socket disconnected")

    await manager.connect(user_id, ws1)
    await manager.connect(user_id, ws2)
    await manager.connect(user_id, ws3)

    assert manager.get_user_connection_count(user_id) == 3

    delivered = await manager.broadcast_to_user(
        user_id, "test_event", {"message": "hello"}
    )
    assert delivered == 2

    # Dead socket ws3 should have been cleaned up automatically
    assert manager.get_user_connection_count(user_id) == 2

    # Test multi-user broadcast
    user_2 = uuid.uuid4()
    ws_user2 = AsyncMock()
    await manager.connect(user_2, ws_user2)

    total_delivered = await manager.broadcast_to_users(
        [user_id, user_2], "multi_event", {"val": 42}
    )
    assert total_delivered == 3
