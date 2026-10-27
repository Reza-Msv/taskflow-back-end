import uuid
from datetime import datetime

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
    org_name: str = "Advanced PM Org",
    org_slug: str = "advanced-pm-org",
    proj_name: str = "Workflow Project",
    proj_key: str = "APM",
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
# 1. Custom Workflow Policies & Transitions
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_custom_workflow_status_and_transitions(
    async_client: AsyncClient,
):
    """Verify custom workflow statuses, transitions, and transition enforcement."""
    _, cookies, headers = await create_user_and_login(
        async_client, "workflow_owner@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="wf-org", proj_key="WFL"
    )

    # 1. List default statuses
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/statuses",
        cookies=cookies,
        headers=headers,
    )
    assert list_resp.status_code == 200
    statuses = list_resp.json()
    assert len(statuses) >= 3
    todo_st = next(s for s in statuses if s["category"] == "todo")
    done_st = next(s for s in statuses if s["category"] == "done")

    # 2. Create custom status
    create_st_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/statuses",
        json={
            "name": "Code Review",
            "category": "in_progress",
            "position": 2,
            "is_default": False,
        },
        cookies=cookies,
        headers=headers,
    )
    assert create_st_resp.status_code == 201
    review_st = create_st_resp.json()
    assert review_st["name"] == "Code Review"
    assert review_st["category"] == "in_progress"
    review_st_id = review_st["id"]

    # 3. Update custom status
    patch_st_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/statuses/{review_st_id}",
        json={"name": "Peer Review"},
        cookies=cookies,
        headers=headers,
    )
    assert patch_st_resp.status_code == 200
    assert patch_st_resp.json()["name"] == "Peer Review"

    # 4. Configure transition rules: To Do -> Peer Review, and Peer Review -> Done
    t1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/transitions",
        json={
            "from_status_id": todo_st["id"],
            "to_status_id": review_st_id,
            "name": "Submit for Review",
        },
        cookies=cookies,
        headers=headers,
    )
    assert t1_resp.status_code == 201

    t2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/transitions",
        json={
            "from_status_id": review_st_id,
            "to_status_id": done_st["id"],
            "name": "Approve and Close",
        },
        cookies=cookies,
        headers=headers,
    )
    assert t2_resp.status_code == 201

    # 5. List transitions
    trans_list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/transitions",
        cookies=cookies,
        headers=headers,
    )
    assert trans_list_resp.status_code == 200
    assert len(trans_list_resp.json()) == 2

    # 6. Create a task and test transition enforcement
    task_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Feature Work"},
        cookies=cookies,
        headers=headers,
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["id"]

    # Trying to jump directly from To Do to Done should be blocked by workflow policy
    invalid_move = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )
    assert invalid_move.status_code == 400
    assert invalid_move.json()["error"]["code"] == "INVALID_STATUS_TRANSITION"

    # Moving To Do -> Peer Review is permitted
    valid_move1 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
        json={"status": "Peer Review"},
        cookies=cookies,
        headers=headers,
    )
    assert valid_move1.status_code == 200

    # Moving Peer Review -> Done is permitted
    valid_move2 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )
    assert valid_move2.status_code == 200

    # 7. Delete status: cannot delete status that contains active tasks
    # Move task back to review_st
    # First add reverse transition so we can move it
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/transitions",
        json={
            "from_status_id": done_st["id"],
            "to_status_id": review_st_id,
        },
        cookies=cookies,
        headers=headers,
    )
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
        json={"status": "Peer Review"},
        cookies=cookies,
        headers=headers,
    )

    del_in_use_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/statuses/{review_st_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_in_use_resp.status_code == 400
    assert del_in_use_resp.json()["error"]["code"] == "STATUS_IN_USE"

    # Move task to done so status is no longer in use
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )

    # Now deletion succeeds
    del_st_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/workflows/statuses/{review_st_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_st_resp.status_code == 200


# -------------------------------------------------------------
# 2. Task Dependency Blocking Integrity
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_dependency_blocking_integrity(
    async_client: AsyncClient,
):
    """Verify tasks cannot be completed when dependent predecessors are unfinished."""
    _, cookies, headers = await create_user_and_login(
        async_client, "dep_integrity@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="dep-org", proj_key="DEP"
    )

    # 1. Create Predecessor task A and Successor task B
    resp_a = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Database Schema Migration"},
        cookies=cookies,
        headers=headers,
    )
    assert resp_a.status_code == 201
    task_a_id = resp_a.json()["id"]

    resp_b = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Run Data Ingestion"},
        cookies=cookies,
        headers=headers,
    )
    assert resp_b.status_code == 201
    task_b_id = resp_b.json()["id"]

    # 2. Establish finish_to_start dependency: task_a -> task_b
    dep_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={
            "predecessor_id": task_a_id,
            "successor_id": task_b_id,
            "dependency_type": "finish_to_start",
        },
        cookies=cookies,
        headers=headers,
    )
    assert dep_resp.status_code == 201
    assert "id" in dep_resp.json()

    # 3. Attempt to mark task B as done while task A is still todo -> blocked!
    move_blocked_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_b_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )
    assert move_blocked_resp.status_code == 400
    assert move_blocked_resp.json()["error"]["code"] == "TASK_BLOCKED_BY_DEPENDENCY"
    err_msg = move_blocked_resp.json()["error"]["message"]
    assert "unfinished predecessor tasks" in err_msg

    # 4. Complete predecessor task A
    move_a_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_a_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )
    assert move_a_resp.status_code == 200

    # 5. Now completing task B succeeds!
    move_b_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_b_id}/move",
        json={"status": "done"},
        cookies=cookies,
        headers=headers,
    )
    assert move_b_resp.status_code == 200

    # 6. Delete task A: verify safe deletion of task that has dependencies
    del_a_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_a_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_a_resp.status_code == 200


# -------------------------------------------------------------
# 3. Task Templates Lifecycle & Instantiation
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_templates_lifecycle_and_instantiation(
    async_client: AsyncClient,
):
    """Verify creating, updating, listing, and instantiating task templates."""
    _, cookies, headers = await create_user_and_login(
        async_client, "template_lead@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="tmpl-org", proj_key="TMP"
    )

    # 1. Create task template with checklist definitions
    create_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/templates",
        json={
            "name": "Security Audit Blueprint",
            "description": "Standard checklist for security review",
            "task_title": "Perform Security Audit",
            "task_description": "Follow checklist items to complete review.",
            "task_type": "task",
            "priority": "high",
            "estimated_points": 5,
            "checklist_definitions": [
                {"title": "Check dependencies for CVEs"},
                {"title": "Verify RBAC permissions"},
                {"title": "Validate input sanitization"},
            ],
        },
        cookies=cookies,
        headers=headers,
    )
    assert create_resp.status_code == 201
    tmpl_data = create_resp.json()
    assert tmpl_data["name"] == "Security Audit Blueprint"
    assert len(tmpl_data["checklist_definitions"]) == 3
    template_id = tmpl_data["id"]

    # 2. List templates
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/templates",
        cookies=cookies,
        headers=headers,
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    # 3. Update template
    patch_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/templates/{template_id}",
        json={"priority": "urgent"},
        cookies=cookies,
        headers=headers,
    )
    assert patch_resp.status_code == 200
    assert patch_resp.json()["priority"] == "urgent"

    # 4. Instantiate task from template
    inst_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/templates/{template_id}/instantiate",
        json={"title": "Q4 Security Audit - Payment Gateway"},
        cookies=cookies,
        headers=headers,
    )
    assert inst_resp.status_code == 201
    new_task = inst_resp.json()
    assert new_task["title"] == "Q4 Security Audit - Payment Gateway"
    assert new_task["priority"] == "urgent"
    assert new_task["estimated_points"] == 5.0
    task_id = new_task["id"]

    # 5. Verify checklist items were cloned onto new task
    task_detail_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        cookies=cookies,
        headers=headers,
    )
    assert task_detail_resp.status_code == 200
    task_detail = task_detail_resp.json()
    assert len(task_detail["checklists"]) == 3
    assert task_detail["checklists"][0]["title"] == "Check dependencies for CVEs"

    # 6. Delete template
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/templates/{template_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200


# -------------------------------------------------------------
# 4. Recurring Tasks Scheduling & Trigger Runner
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_recurring_tasks_scheduling_and_trigger(
    async_client: AsyncClient,
):
    """Verify recurring task creation, scheduling calculation, and trigger runner."""
    _, cookies, headers = await create_user_and_login(
        async_client, "recurring_lead@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="rec-org", proj_key="REC"
    )

    # 1. Create weekly recurring rule
    create_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/recurring-tasks",
        json={
            "title": "Weekly Team Backlog Grooming",
            "description": "Review top sprint backlog items",
            "recurrence_pattern": "weekly",
            "interval_value": 1,
            "priority": "medium",
            "estimated_points": 2,
        },
        cookies=cookies,
        headers=headers,
    )
    assert create_resp.status_code == 201
    rule_data = create_resp.json()
    assert rule_data["title"] == "Weekly Team Backlog Grooming"
    assert rule_data["recurrence_pattern"] == "weekly"
    rule_id = rule_data["id"]
    initial_next_run = datetime.fromisoformat(rule_data["next_run_at"])

    # 2. List recurring rules
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/recurring-tasks",
        cookies=cookies,
        headers=headers,
    )
    assert list_resp.status_code == 200
    assert len(list_resp.json()) == 1

    # 3. Trigger recurring rule runner
    trigger_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/recurring-tasks/{rule_id}/trigger",
        cookies=cookies,
        headers=headers,
    )
    assert trigger_resp.status_code == 201
    result = trigger_resp.json()
    assert result["task"]["title"] == "Weekly Team Backlog Grooming"
    updated_rule = result["rule"]
    assert updated_rule["last_run_at"] is not None
    next_run = datetime.fromisoformat(updated_rule["next_run_at"])
    # next_run should be approximately 7 days after trigger run
    assert next_run > initial_next_run

    # 4. Delete recurring rule
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/recurring-tasks/{rule_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200


# -------------------------------------------------------------
# 5. Automation Rules, Dry-Run, and Execution Engine
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_event_automations_and_loop_guard(
    async_client: AsyncClient,
):
    """Verify event-driven automation rules, test simulation, and execution auditing."""
    _, cookies, headers = await create_user_and_login(
        async_client, "auto_lead@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="auto-org", proj_key="AUT"
    )

    # 1. Create automation rule: when status becomes in_progress,
    # update priority to urgent
    rule_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/automations/rules",
        json={
            "name": "Auto-escalate In-Progress Tasks",
            "trigger_event": "task_status_changed",
            "trigger_conditions": {"status": "in_progress"},
            "action_type": "update_priority",
            "action_payload": {"priority": "urgent"},
            "is_active": True,
        },
        cookies=cookies,
        headers=headers,
    )
    assert rule_resp.status_code == 201
    rule_data = rule_resp.json()
    rule_id = rule_data["id"]

    # 2. Create task
    task_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Database Optimization", "priority": "low"},
        cookies=cookies,
        headers=headers,
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["id"]

    # 3. Dry-run test against task before status change
    dry_run_1 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/automations/rules/{rule_id}/test",
        json={"work_item_id": task_id},
        cookies=cookies,
        headers=headers,
    )
    assert dry_run_1.status_code == 200
    assert dry_run_1.json()["matched"] is False

    # 4. Move task status to in_progress -> should trigger the automation!
    move_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
        json={"status": "in_progress"},
        cookies=cookies,
        headers=headers,
    )
    assert move_resp.status_code == 200

    # 5. Verify the task's priority was automatically updated to urgent
    task_check = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        cookies=cookies,
        headers=headers,
    )
    assert task_check.status_code == 200
    assert task_check.json()["priority"] == "urgent"

    # 6. Verify audit logs recorded the execution
    logs_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/automations/logs",
        cookies=cookies,
        headers=headers,
    )
    assert logs_resp.status_code == 200
    logs = logs_resp.json()
    assert len(logs) >= 1
    assert logs[0]["status"] == "success"
    assert "urgent" in logs[0]["action_taken"]


# -------------------------------------------------------------
# 6. Bulk Task Operations
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_bulk_task_operations(
    async_client: AsyncClient,
):
    """Verify batch updates, dependency blocking in bulk, and max boundary limits."""
    user_data, cookies, headers = await create_user_and_login(
        async_client, "bulk_lead@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="bulk-org", proj_key="BLK"
    )

    # 1. Create 3 tasks
    task_ids: list[str] = []
    for i in range(3):
        r = await async_client.post(
            f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
            json={"title": f"Batch Task {i + 1}", "priority": "low"},
            cookies=cookies,
            headers=headers,
        )
        assert r.status_code == 201
        task_ids.append(r.json()["id"])

    # 2. Bulk update priority to "high"
    bulk_pri_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/bulk-operation",
        json={
            "task_ids": task_ids,
            "operation": "update_priority",
            "payload": {"priority": "high"},
        },
        cookies=cookies,
        headers=headers,
    )
    assert bulk_pri_resp.status_code == 200
    res_data = bulk_pri_resp.json()
    assert res_data["successful_count"] == 3
    assert res_data["failed_count"] == 0

    # 3. Bulk assign user
    bulk_ass_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/bulk-operation",
        json={
            "task_ids": task_ids,
            "operation": "assign_user",
            "payload": {"assignee_id": user_data["id"]},
        },
        cookies=cookies,
        headers=headers,
    )
    assert bulk_ass_resp.status_code == 200
    assert bulk_ass_resp.json()["successful_count"] == 3

    # 4. Dependency blocking in bulk operation
    # Add dependency: task_ids[0] (pred) -> task_ids[1] (succ)
    await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/dependencies",
        json={
            "predecessor_id": task_ids[0],
            "successor_id": task_ids[1],
            "dependency_type": "finish_to_start",
        },
        cookies=cookies,
        headers=headers,
    )

    # Try bulk update status to "done" for task_ids[1] and task_ids[2]
    # task_ids[1] is blocked, task_ids[2] is unblocked
    bulk_status_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/bulk-operation",
        json={
            "task_ids": [task_ids[1], task_ids[2]],
            "operation": "update_status",
            "payload": {"status": "done"},
        },
        cookies=cookies,
        headers=headers,
    )
    assert bulk_status_resp.status_code == 200
    st_result = bulk_status_resp.json()
    assert st_result["successful_count"] == 1
    assert st_result["failed_count"] == 1
    assert st_result["errors"][0]["error_code"] == "TASK_BLOCKED_BY_DEPENDENCY"

    # 5. Boundary validation: > 50 tasks rejected with BULK_LIMIT_EXCEEDED
    too_many_ids = [str(uuid.uuid4()) for _ in range(51)]
    over_limit_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/bulk-operation",
        json={
            "task_ids": too_many_ids,
            "operation": "update_priority",
            "payload": {"priority": "low"},
        },
        cookies=cookies,
        headers=headers,
    )
    assert over_limit_resp.status_code == 400
    assert over_limit_resp.json()["error"]["code"] == "BULK_LIMIT_EXCEEDED"
