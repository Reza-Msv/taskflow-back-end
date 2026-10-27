"""PostgreSQL database integration and end-to-end integration verification.

This test connects to the configured local PostgreSQL database (taskflow_db)
and exercises real database-backed transactions, constraints, sequences,
cookies, CSRF, and both project-level and task-level mentionable members endpoints.
"""

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from taskflow.core.config import settings
from taskflow.main import app


@pytest.mark.asyncio
async def test_real_postgres_connection_and_catalog():
    """Verify local PostgreSQL instance, alembic revision, and table count."""
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.connect() as conn:
        res = await conn.execute(text("SELECT current_database(), current_user, version()"))
        db, user, ver = res.fetchone()
        assert db == settings.POSTGRES_DB
        assert "PostgreSQL" in ver

        # Verify Alembic version is at head (20261027_0009)
        rev_res = await conn.execute(text("SELECT version_num FROM alembic_version"))
        revs = [r[0] for r in rev_res.fetchall()]
        assert "20261027_0009" in revs

        # Verify 32 application tables exist
        tbl_res = await conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        )
        tbl_count = tbl_res.scalar()
        assert tbl_count >= 32
    await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_full_workflow_and_mentionable_endpoints():
    """Test full registration, workspace, project, task, and mentions against app."""
    # Use real application with ASGITransport (no dependency overrides)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Register test user
        unique_suffix = uuid.uuid4().hex[:8]
        email = f"audit_user_{unique_suffix}@taskflow.dev"
        password = "SecurePassword123!"
        full_name = "Audit Test User"

        reg_resp = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password, "full_name": full_name},
        )
        assert reg_resp.status_code == 201
        user_data = reg_resp.json()["user"]
        user_id = user_data["id"]

        # 2. Login to obtain session cookie and CSRF token
        login_resp = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        assert login_resp.status_code == 200
        login_data = login_resp.json()
        assert "csrf_token" in login_data
        csrf_token = login_data["csrf_token"]
        session_cookie = login_resp.cookies[settings.SESSION_COOKIE_NAME]

        cookies = {settings.SESSION_COOKIE_NAME: session_cookie}
        headers = {"X-CSRF-Token": csrf_token}

        # 3. Create Organization
        org_slug = f"audit-org-{unique_suffix}"
        org_resp = await client.post(
            "/api/v1/organizations",
            json={"name": "Audit Test Organization", "slug": org_slug},
            cookies=cookies,
            headers=headers,
        )
        assert org_resp.status_code == 201
        org_id = org_resp.json()["id"]

        # 4. Create Project
        proj_key = f"AUD{unique_suffix[:3].upper()}"
        proj_resp = await client.post(
            f"/api/v1/organizations/{org_id}/projects",
            json={
                "name": "Audit Test Project",
                "key": proj_key,
                "description": "Integration test project",
                "is_private": False,
            },
            cookies=cookies,
            headers=headers,
        )
        assert proj_resp.status_code == 201
        proj_id = proj_resp.json()["id"]

        # 5. Create Task
        task_resp = await client.post(
            f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks",
            json={
                "title": "Integration Test Task",
                "type": "task",
                "priority": "high",
                "description": "Task created during local PostgreSQL database audit",
            },
            cookies=cookies,
            headers=headers,
        )
        assert task_resp.status_code == 201
        task_data = task_resp.json()
        task_id = task_data["id"]
        assert task_data["issue_key"].startswith(proj_key)

        # 6. Verify project-level mentionable members
        proj_mention_resp = await client.get(
            f"/api/v1/organizations/{org_id}/projects/{proj_id}/mentionable-members",
            cookies=cookies,
        )
        assert proj_mention_resp.status_code == 200
        members = proj_mention_resp.json()
        assert any(m["id"] == user_id for m in members)

        # 7. Verify task-level mentionable members (Repaired integration endpoint)
        task_mention_resp = await client.get(
            f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task_id}/mentionable-members",
            cookies=cookies,
        )
        assert task_mention_resp.status_code == 200
        task_members = task_mention_resp.json()
        assert any(m["id"] == user_id for m in task_members)

        # 8. Post a comment with mention
        comment_resp = await client.post(
            f"/api/v1/organizations/{org_id}/projects/{proj_id}/tasks/{task_id}/comments",
            json={
                "content": f"Hello @{user_data['full_name']}! Integration test verification.",
                "mentioned_user_ids": [user_id],
            },
            cookies=cookies,
            headers=headers,
        )
        assert comment_resp.status_code == 201
        comment_data = comment_resp.json()
        assert comment_data["work_item_id"] == task_id

        # 9. Clean up test organization (cascades to projects, tasks, comments, memberships)
        del_resp = await client.delete(
            f"/api/v1/organizations/{org_id}",
            cookies=cookies,
            headers=headers,
        )
        assert del_resp.status_code in (200, 204)
