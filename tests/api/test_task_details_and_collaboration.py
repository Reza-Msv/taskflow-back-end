import io

import pytest
from httpx import AsyncClient

from taskflow.core.config import settings


async def create_user_and_login(
    client: AsyncClient,
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "Test User",
) -> tuple[dict, dict, dict]:
    """Helper creating a user and logging in. Returns user, cookies, headers."""
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
    """Helper creating an organization and a project. Returns org_id, project_id."""
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
# 1. Task Details Aggregation
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_details_aggregation(async_client: AsyncClient):
    """
    Task details returns full context including subtasks, checklists,
    attachments, and comment metrics.
    """
    owner, cookies, headers = await create_user_and_login(
        async_client, email="detail_owner@example.com", full_name="Detail Owner"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="detail-org", proj_key="DTL"
    )

    # 1. Create parent task
    task_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={
            "title": "Primary Task",
            "priority": "high",
            "description": "Main task spec",
        },
        cookies=cookies,
        headers=headers,
    )
    assert task_resp.status_code == 201
    task_id = task_resp.json()["id"]

    # 2. Add checklist items
    chk1_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists",
        json={"title": "Item 1", "position": 0},
        cookies=cookies,
        headers=headers,
    )
    assert chk1_resp.status_code == 201
    chk1_id = chk1_resp.json()["id"]

    chk2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists",
        json={"title": "Item 2", "position": 1},
        cookies=cookies,
        headers=headers,
    )
    assert chk2_resp.status_code == 201

    # Mark Item 1 complete
    await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists/{chk1_id}",
        json={"is_completed": True},
        cookies=cookies,
        headers=headers,
    )

    # 3. Add subtask
    sub_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/subtasks",
        json={"title": "Subtask Alpha"},
        cookies=cookies,
        headers=headers,
    )
    assert sub_resp.status_code == 201

    # 4. Add comment
    comm_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments",
        json={"content": "Initial kickoff discussion"},
        cookies=cookies,
        headers=headers,
    )
    assert comm_resp.status_code == 201

    # 5. Add attachment
    fake_file = io.BytesIO(b"file content mock")
    att_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments",
        files={"file": ("test_doc.txt", fake_file, "text/plain")},
        cookies=cookies,
        headers=headers,
    )
    assert att_resp.status_code == 201

    # 6. Retrieve full task details
    detail_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
        cookies=cookies,
        headers=headers,
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()

    assert detail["title"] == "Primary Task"
    assert detail["project"]["key"] == "DTL"
    assert detail["reporter"]["id"] == owner["id"]

    # Subtasks
    assert detail["subtasks_count"] == 1
    assert len(detail["subtasks"]) == 1
    assert detail["subtasks"][0]["title"] == "Subtask Alpha"

    # Checklists
    assert detail["checklists_count"] == 2
    assert detail["completed_checklists_count"] == 1
    assert len(detail["checklists"]) == 2

    # Attachments
    assert detail["attachments_count"] == 1
    assert len(detail["attachments"]) == 1
    assert detail["attachments"][0]["file_name"] == "test_doc.txt"

    # Comments
    assert detail["comments_count"] == 1


# -------------------------------------------------------------
# 2. Task Comments CRUD, Permissions & History
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_comment_crud_permissions_and_audit(async_client: AsyncClient):
    """
    Comment author can edit/delete; admins can manage; unauthorized users cannot.
    Audit stream tracks comment lifecycle.
    """
    owner, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="comm_owner@example.com", full_name="Org Lead"
    )
    member, member_cookies, member_headers = await create_user_and_login(
        async_client, email="comm_member@example.com", full_name="Team Dev"
    )
    other_member, other_cookies, other_headers = await create_user_and_login(
        async_client, email="comm_other@example.com", full_name="Other Dev"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, owner_cookies, owner_headers, org_slug="comm-org", proj_key="CMM"
    )

    # Add members to organization
    for u in (member, other_member):
        inv_resp = await async_client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            json={"email": u["email"], "role": "member"},
            cookies=owner_cookies,
            headers=owner_headers,
        )
        assert inv_resp.status_code == 201
        token = inv_resp.json()["token"]
        accept_cookies = {
            settings.SESSION_COOKIE_NAME: member_cookies[settings.SESSION_COOKIE_NAME]
            if u == member
            else other_cookies[settings.SESSION_COOKIE_NAME]
        }
        accept_headers = {
            "X-CSRF-Token": member_headers["X-CSRF-Token"]
            if u == member
            else other_headers["X-CSRF-Token"]
        }
        acc_resp = await async_client.post(
            f"/api/v1/organizations/invitations/accept?token={token}",
            cookies=accept_cookies,
            headers=accept_headers,
        )
        assert acc_resp.status_code == 200

    # Create task
    t_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Commentable Task"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    task_id = t_resp.json()["id"]

    # Member posts comment
    comm_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments",
        json={"content": "Here is my first feedback."},
        cookies=member_cookies,
        headers=member_headers,
    )
    assert comm_resp.status_code == 201
    comm = comm_resp.json()
    comment_id = comm["id"]
    assert comm["content"] == "Here is my first feedback."
    assert comm["is_edited"] is False
    assert comm["author"]["id"] == member["id"]

    # Other member tries to edit -> 403 Forbidden
    other_edit = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments/{comment_id}",
        json={"content": "Malicious edit attempt"},
        cookies=other_cookies,
        headers=other_headers,
    )
    assert other_edit.status_code == 403

    # Original author edits comment -> 200 OK & is_edited is True
    edit_resp = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments/{comment_id}",
        json={"content": "Updated feedback with revisions."},
        cookies=member_cookies,
        headers=member_headers,
    )
    assert edit_resp.status_code == 200
    assert edit_resp.json()["content"] == "Updated feedback with revisions."
    assert edit_resp.json()["is_edited"] is True

    # Other member tries to delete -> 403 Forbidden
    other_del = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments/{comment_id}",
        cookies=other_cookies,
        headers=other_headers,
    )
    assert other_del.status_code == 403

    # Org owner CAN delete comment -> 200 OK
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments/{comment_id}",
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert del_resp.status_code == 200

    # Verify history logs comment events
    hist_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/history",
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert hist_resp.status_code == 200
    fields = [item["field"] for item in hist_resp.json()["items"]]
    assert "comment_created" in fields
    assert "comment_edited" in fields
    assert "comment_deleted" in fields


# -------------------------------------------------------------
# 3. User Mentions & Autocomplete
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_mentions_and_notifications(async_client: AsyncClient):
    """
    Mentioning a user in a comment validates membership, persists mention,
    and dispatches an in-app notification without self-notifying or duplicates.
    """
    owner, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="mention_owner@example.com", full_name="Project Lead"
    )
    dev1, dev1_cookies, dev1_headers = await create_user_and_login(
        async_client, email="dev1@example.com", full_name="Alice Developer"
    )
    outsider, _, _ = await create_user_and_login(
        async_client, email="outsider_m@example.com", full_name="Outsider Bob"
    )
    org_id, project_id = await setup_org_and_project(
        async_client,
        owner_cookies,
        owner_headers,
        org_slug="mention-org",
        proj_key="MNT",
    )

    # Add dev1 to org
    inv_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        json={"email": dev1["email"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    token = inv_resp.json()["token"]
    await async_client.post(
        f"/api/v1/organizations/invitations/accept?token={token}",
        cookies=dev1_cookies,
        headers=dev1_headers,
    )

    # Test mentionable members autocomplete
    ment_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/mentionable-members",
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert ment_resp.status_code == 200
    members = ment_resp.json()
    member_ids = [m["id"] for m in members]
    assert owner["id"] in member_ids
    assert dev1["id"] in member_ids
    assert outsider["id"] not in member_ids

    # Create task
    t_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Feature Review"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    task_id = t_resp.json()["id"]

    # Try mentioning outsider -> 400 INVALID_MENTION
    bad_ment = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments",
        json={
            "content": "Please review @Outsider",
            "mentioned_user_ids": [outsider["id"]],
        },
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert bad_ment.status_code == 400
    assert bad_ment.json()["error"]["code"] == "INVALID_MENTION"

    # Mention dev1 (with duplicate ID in request)
    good_ment = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments",
        json={
            "content": "Hey @Alice please take a look",
            "mentioned_user_ids": [dev1["id"], dev1["id"]],  # duplicate test
        },
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert good_ment.status_code == 201
    comment_data = good_ment.json()
    assert len(comment_data["mentions"]) == 1
    assert comment_data["mentions"][0]["user_id"] == dev1["id"]

    # Verify dev1 received mention notification
    notif_resp = await async_client.get(
        "/api/v1/notifications",
        cookies=dev1_cookies,
        headers=dev1_headers,
    )
    assert notif_resp.status_code == 200
    notifs = notif_resp.json()["items"]
    assert len(notifs) >= 1
    mention_notif = next((n for n in notifs if n["type"] == "task_mentioned"), None)
    assert mention_notif is not None
    assert mention_notif["actor"]["id"] == owner["id"]
    assert "mentioned you" in mention_notif["message"]

    # Verify owner did NOT receive self-notification
    owner_notifs = await async_client.get(
        "/api/v1/notifications",
        cookies=owner_cookies,
        headers=owner_headers,
    )
    assert len(owner_notifs.json()["items"]) == 0


# -------------------------------------------------------------
# 4. Attachments Upload, Download & Safe Storage
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_upload_download_and_deletion(async_client: AsyncClient):
    """
    Test uploading safe file types, downloading, rejecting unsupported types,
    and deleting attachments with permission enforcement.
    """
    owner, cookies, headers = await create_user_and_login(
        async_client, email="att_owner@example.com", full_name="Uploader User"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="att-org", proj_key="ATT"
    )

    t_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Attachment Task"},
        cookies=cookies,
        headers=headers,
    )
    task_id = t_resp.json()["id"]

    # 1. Reject unsupported executable file (.exe)
    bad_file = io.BytesIO(b"binary executable payload")
    bad_upload = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments",
        files={"file": ("malware.exe", bad_file, "application/octet-stream")},
        cookies=cookies,
        headers=headers,
    )
    assert bad_upload.status_code == 400
    assert bad_upload.json()["error"]["code"] == "UNSUPPORTED_FILE_TYPE"

    # 2. Upload valid PDF attachment
    pdf_content = b"%PDF-1.4 sample pdf content for test"
    good_file = io.BytesIO(pdf_content)
    upload_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments",
        files={"file": ("spec_document.pdf", good_file, "application/pdf")},
        cookies=cookies,
        headers=headers,
    )
    assert upload_resp.status_code == 201
    attachment = upload_resp.json()
    att_id = attachment["id"]
    assert attachment["file_name"] == "spec_document.pdf"
    assert attachment["file_size"] == len(pdf_content)
    assert attachment["content_type"] == "application/pdf"
    assert attachment["uploader"]["id"] == owner["id"]

    # 3. List attachments
    list_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments",
        cookies=cookies,
        headers=headers,
    )
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 1

    # 4. Download attachment
    dl_resp = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments/{att_id}/download",
        cookies=cookies,
        headers=headers,
    )
    assert dl_resp.status_code == 200
    assert dl_resp.content == pdf_content
    assert "application/pdf" in dl_resp.headers["content-type"]

    # 5. Delete attachment
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments/{att_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200

    # 6. Verify deleted
    get_after = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments/{att_id}",
        cookies=cookies,
        headers=headers,
    )
    assert get_after.status_code == 404


# -------------------------------------------------------------
# 5. Checklists CRUD & Ordering
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_checklist_crud_and_ordering(async_client: AsyncClient):
    """
    Verify creating, ordering, toggling completion, and deleting checklist items.
    """
    owner, cookies, headers = await create_user_and_login(
        async_client, email="chk_owner@example.com"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, cookies, headers, org_slug="chk-org", proj_key="CHK"
    )

    t_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
        json={"title": "Checklist Test Task"},
        cookies=cookies,
        headers=headers,
    )
    task_id = t_resp.json()["id"]

    # Create 3 items
    c1 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists",
        json={"title": "Step 1: Planning", "position": 1},
        cookies=cookies,
        headers=headers,
    )
    assert c1.status_code == 201

    c2 = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists",
        json={"title": "Step 2: Execution", "position": 2},
        cookies=cookies,
        headers=headers,
    )
    assert c2.status_code == 201
    c2_id = c2.json()["id"]

    # Toggle completion on Step 2
    update_c2 = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists/{c2_id}",
        json={"is_completed": True},
        cookies=cookies,
        headers=headers,
    )
    assert update_c2.status_code == 200
    assert update_c2.json()["is_completed"] is True

    # List items
    chk_list = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists",
        cookies=cookies,
        headers=headers,
    )
    assert chk_list.status_code == 200
    items = chk_list.json()["items"]
    assert len(items) == 2
    assert items[0]["title"] == "Step 1: Planning"
    assert items[1]["is_completed"] is True

    # Delete Step 2
    del_resp = await async_client.delete(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists/{c2_id}",
        cookies=cookies,
        headers=headers,
    )
    assert del_resp.status_code == 200

    chk_list_after = await async_client.get(
        f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists",
        cookies=cookies,
        headers=headers,
    )
    assert chk_list_after.json()["total"] == 1


# -------------------------------------------------------------
# 6. Subtask Hierarchy & Cycle Prevention
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_subtask_hierarchy_and_cycle_prevention(async_client: AsyncClient):
    """
    Subtasks prevent self-parenting, circular hierarchy,
    and cross-project relationships.
    """
    owner, cookies, headers = await create_user_and_login(
        async_client, email="sub_owner@example.com"
    )
    org_id, project1 = await setup_org_and_project(
        async_client, cookies, headers, org_slug="sub-org", proj_key="SUB"
    )

    # Second project in same org
    proj2_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Second Project", "key": "SEC"},
        cookies=cookies,
        headers=headers,
    )
    project2 = proj2_resp.json()["id"]

    # Task A in Project 1
    tA_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project1}/tasks",
        json={"title": "Task A"},
        cookies=cookies,
        headers=headers,
    )
    taskA_id = tA_resp.json()["id"]

    # Task B in Project 1
    tB_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project1}/tasks",
        json={"title": "Task B"},
        cookies=cookies,
        headers=headers,
    )
    taskB_id = tB_resp.json()["id"]

    # Task C in Project 2
    tC_resp = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects/{project2}/tasks",
        json={"title": "Task C"},
        cookies=cookies,
        headers=headers,
    )
    taskC_id = tC_resp.json()["id"]

    # 1. Prevent self-parenting
    self_parent = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project1}/tasks/{taskA_id}",
        json={"parent_id": taskA_id},
        cookies=cookies,
        headers=headers,
    )
    assert self_parent.status_code == 400
    assert self_parent.json()["error"]["code"] == "INVALID_PARENT"

    # 2. Prevent cross-project parenting
    cross_parent = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project1}/tasks/{taskA_id}",
        json={"parent_id": taskC_id},
        cookies=cookies,
        headers=headers,
    )
    assert cross_parent.status_code == 400
    assert cross_parent.json()["error"]["code"] == "CROSS_PROJECT_HIERARCHY"

    # 3. Valid parent: Task B becomes parent of Task A
    valid_parent = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project1}/tasks/{taskA_id}",
        json={"parent_id": taskB_id},
        cookies=cookies,
        headers=headers,
    )
    assert valid_parent.status_code == 200
    assert valid_parent.json()["parent_id"] == taskB_id

    # 4. Prevent circular hierarchy: setting Task B's parent to Task A
    circular = await async_client.patch(
        f"/api/v1/organizations/{org_id}/projects/{project1}/tasks/{taskB_id}",
        json={"parent_id": taskA_id},
        cookies=cookies,
        headers=headers,
    )
    assert circular.status_code == 400
    assert circular.json()["error"]["code"] == "CIRCULAR_HIERARCHY"


# -------------------------------------------------------------
# 7. Notifications Read State & Unread Counts
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_notification_read_lifecycle(async_client: AsyncClient):
    """
    Test listing notifications, unread count badge, marking single read,
    and marking all read.
    """
    owner, owner_cookies, owner_headers = await create_user_and_login(
        async_client, email="notif_owner@example.com", full_name="Lead Assigner"
    )
    assignee, asg_cookies, asg_headers = await create_user_and_login(
        async_client, email="notif_asg@example.com", full_name="Worker Bee"
    )
    org_id, project_id = await setup_org_and_project(
        async_client, owner_cookies, owner_headers, org_slug="notif-org", proj_key="NTF"
    )

    # Add assignee to org
    inv = await async_client.post(
        f"/api/v1/organizations/{org_id}/invitations",
        json={"email": assignee["email"], "role": "member"},
        cookies=owner_cookies,
        headers=owner_headers,
    )
    await async_client.post(
        f"/api/v1/organizations/invitations/accept?token={inv.json()['token']}",
        cookies=asg_cookies,
        headers=asg_headers,
    )

    # Owner creates two tasks assigned to assignee
    for i in range(2):
        await async_client.post(
            f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
            json={"title": f"Assigned Task {i + 1}", "assignee_id": assignee["id"]},
            cookies=owner_cookies,
            headers=owner_headers,
        )

    # Verify unread count = 2
    count_resp = await async_client.get(
        "/api/v1/notifications/unread-count",
        cookies=asg_cookies,
        headers=asg_headers,
    )
    assert count_resp.status_code == 200
    assert count_resp.json()["unread_count"] == 2

    # List notifications
    notif_list = await async_client.get(
        "/api/v1/notifications",
        cookies=asg_cookies,
        headers=asg_headers,
    )
    items = notif_list.json()["items"]
    assert len(items) == 2

    # Mark first notification read
    mark_one = await async_client.patch(
        f"/api/v1/notifications/{items[0]['id']}/read",
        cookies=asg_cookies,
        headers=asg_headers,
    )
    assert mark_one.status_code == 200
    assert mark_one.json()["is_read"] is True

    # Verify count is now 1
    count_resp2 = await async_client.get(
        "/api/v1/notifications/unread-count",
        cookies=asg_cookies,
        headers=asg_headers,
    )
    assert count_resp2.json()["unread_count"] == 1

    # Mark all read
    read_all = await async_client.post(
        "/api/v1/notifications/read-all",
        cookies=asg_cookies,
        headers=asg_headers,
    )
    assert read_all.status_code == 200

    # Verify count is now 0
    count_resp3 = await async_client.get(
        "/api/v1/notifications/unread-count",
        cookies=asg_cookies,
        headers=asg_headers,
    )
    assert count_resp3.json()["unread_count"] == 0


# -------------------------------------------------------------
# 8. Cross-Tenant Isolation
# -------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_tenant_isolation_collaboration(async_client: AsyncClient):
    """
    User in Org B cannot read or modify comments, checklists, attachments,
    or tasks in Org A.
    """
    ownerA, cookiesA, headersA = await create_user_and_login(
        async_client, email="tenantA@example.com"
    )
    ownerB, cookiesB, headersB = await create_user_and_login(
        async_client, email="tenantB@example.com"
    )
    orgA, projA = await setup_org_and_project(
        async_client, cookiesA, headersA, org_slug="tenant-a", proj_key="TNA"
    )
    orgB, projB = await setup_org_and_project(
        async_client, cookiesB, headersB, org_slug="tenant-b", proj_key="TNB"
    )

    # Create task in Org A
    t_resp = await async_client.post(
        f"/api/v1/organizations/{orgA}/projects/{projA}/tasks",
        json={"title": "Confidential Task Org A"},
        cookies=cookiesA,
        headers=headersA,
    )
    taskA_id = t_resp.json()["id"]

    # User B tries to view details of Org A task -> 403 Forbidden
    cross_get = await async_client.get(
        f"/api/v1/organizations/{orgA}/projects/{projA}/tasks/{taskA_id}",
        cookies=cookiesB,
        headers=headersB,
    )
    assert cross_get.status_code == 403

    # User B tries to comment on Org A task -> 403 Forbidden
    cross_comm = await async_client.post(
        f"/api/v1/organizations/{orgA}/projects/{projA}/tasks/{taskA_id}/comments",
        json={"content": "Intrusion attempt"},
        cookies=cookiesB,
        headers=headersB,
    )
    assert cross_comm.status_code == 403

    # User B tries to upload attachment on Org A task -> 403 Forbidden
    fake_f = io.BytesIO(b"intrusion payload")
    cross_att = await async_client.post(
        f"/api/v1/organizations/{orgA}/projects/{projA}/tasks/{taskA_id}/attachments",
        files={"file": ("infiltrate.txt", fake_f, "text/plain")},
        cookies=cookiesB,
        headers=headersB,
    )
    assert cross_att.status_code == 403
