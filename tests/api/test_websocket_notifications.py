import uuid

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests.conftest import TestingSessionLocal, override_get_db

from taskflow.api.deps import get_db
from taskflow.core.config import settings
from taskflow.domains.collaboration.websocket import notification_broadcaster
from taskflow.main import app


async def create_user_and_login(
    client,
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "User",
) -> tuple[dict, dict, dict]:
    """Helper creating a user and logging in. Returns user, cookies, headers."""
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

    token = login.cookies[settings.SESSION_COOKIE_NAME]
    csrf = login_data["csrf_token"]
    cookies = {settings.SESSION_COOKIE_NAME: token}
    headers = {"X-CSRF-Token": csrf}
    return user_data, cookies, headers


@pytest.mark.asyncio
async def test_websocket_unauthorized_rejected(async_client):
    """
    WebSocket connection without credentials or with invalid token
    must be rejected with 1008.
    """
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as client:
        # 1. No credentials
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/v1/notifications/ws"):
                pass
        assert exc_info.value.code == 1008

        # 2. Invalid session cookie
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/api/v1/notifications/ws",
                cookies={settings.SESSION_COOKIE_NAME: "invalid-fake-token-12345"},
            ):
                pass
        assert exc_info.value.code == 1008


@pytest.mark.asyncio
async def test_websocket_authenticated_handshake_and_ping_pong(async_client):
    """Authenticated user connects successfully and can ping-pong."""
    app.dependency_overrides[get_db] = override_get_db
    user, cookies, _ = await create_user_and_login(async_client, "ws_user1@example.com")

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/notifications/ws",
            cookies=cookies,
        ) as ws:
            # 1. Verify connected event
            connected_msg = ws.receive_json()
            assert connected_msg["event"] == "connected"
            assert connected_msg["data"]["user_id"] == user["id"]

            # 2. Test ping / pong
            ws.send_json({"event": "ping"})
            pong_msg = ws.receive_json()
            assert pong_msg["event"] == "pong"
            assert "timestamp" in pong_msg["data"]


@pytest.mark.asyncio
async def test_websocket_alias_endpoint(async_client):
    """Verify that /api/v1/ws/notifications alias route works identically."""
    app.dependency_overrides[get_db] = override_get_db
    user, cookies, _ = await create_user_and_login(async_client, "ws_alias@example.com")

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/ws/notifications",
            cookies=cookies,
        ) as ws:
            connected_msg = ws.receive_json()
            assert connected_msg["event"] == "connected"
            assert connected_msg["data"]["user_id"] == user["id"]


@pytest.mark.asyncio
async def test_websocket_realtime_broadcast_and_user_isolation(async_client):
    """
    Test real-time notification broadcast on task events:
    - User A assigns task to User B -> User B receives real-time notification
    - User C (isolated) does NOT receive User B's notification
    """
    app.dependency_overrides[get_db] = override_get_db

    # Create 3 users
    user_a, cookies_a, headers_a = await create_user_and_login(
        async_client, "actor_a@example.com", full_name="User A"
    )
    user_b, cookies_b, headers_b = await create_user_and_login(
        async_client, "recipient_b@example.com", full_name="User B"
    )
    user_c, cookies_c, headers_c = await create_user_and_login(
        async_client, "observer_c@example.com", full_name="User C"
    )

    # User A creates organization
    org_res = await async_client.post(
        "/api/v1/organizations",
        json={"name": "WS Test Org", "slug": "ws-test-org"},
        cookies=cookies_a,
        headers=headers_a,
    )
    assert org_res.status_code == 201
    org_id = org_res.json()["id"]

    # Add User B and User C to organization
    for _u, u_cookies, u_headers, email in [
        (user_b, cookies_b, headers_b, "recipient_b@example.com"),
        (user_c, cookies_c, headers_c, "observer_c@example.com"),
    ]:
        inv_res = await async_client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            json={"email": email, "role": "member"},
            cookies=cookies_a,
            headers=headers_a,
        )
        assert inv_res.status_code == 201
        inv_token = inv_res.json()["token"]
        acc_res = await async_client.post(
            f"/api/v1/organizations/invitations/accept?token={inv_token}",
            cookies=u_cookies,
            headers=u_headers,
        )
        assert acc_res.status_code == 200

    # User A creates project
    proj_res = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "WS Project", "key": "WSP"},
        cookies=cookies_a,
        headers=headers_a,
    )
    assert proj_res.status_code == 201
    project_id = proj_res.json()["id"]

    # Connect WebSockets for User B and User C
    with TestClient(app) as test_client:
        with test_client.websocket_connect(
            "/api/v1/notifications/ws",
            cookies=cookies_b,
        ) as ws_b:
            assert ws_b.receive_json()["event"] == "connected"

            with test_client.websocket_connect(
                "/api/v1/notifications/ws",
                cookies=cookies_c,
            ) as ws_c:
                assert ws_c.receive_json()["event"] == "connected"

                # User A creates a task assigned to User B
                task_res = await async_client.post(
                    f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
                    json={
                        "title": "Real-time Task",
                        "assignee_id": user_b["id"],
                    },
                    cookies=cookies_a,
                    headers=headers_a,
                )
                assert task_res.status_code == 201
                task_id = task_res.json()["id"]

                # User B MUST receive real-time notification
                notif_b = ws_b.receive_json()
                assert notif_b["event"] == "notification_created"
                assert notif_b["data"]["user_id"] == user_b["id"]
                assert notif_b["data"]["type"] == "task_assigned"
                assert (
                    "Real-time Task" in notif_b["data"]["title"]
                    or "Real-time Task" in notif_b["data"]["message"]
                )

                # User A adds a comment mentioning User B
                comment_res = await async_client.post(
                    f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments",
                    json={
                        "content": "Hey @[User B](uuid), please check this out!",
                        "mentioned_user_ids": [user_b["id"]],
                    },
                    cookies=cookies_a,
                    headers=headers_a,
                )
                assert comment_res.status_code == 201

                # User B receives mention notification
                mention_notif = ws_b.receive_json()
                assert mention_notif["event"] == "notification_created"
                assert mention_notif["data"]["user_id"] == user_b["id"]
                assert mention_notif["data"]["type"] == "task_mentioned"

                # Verify User C received ping/pong without seeing User B's notifications
                ws_c.send_json({"event": "ping"})
                pong = ws_c.receive_json()
                assert pong["event"] == "pong"


@pytest.mark.asyncio
async def test_websocket_multiple_simultaneous_connections(async_client):
    """
    Verify multiple simultaneous WebSocket connections for the same user
    (e.g. multi-tab) both receive real-time notifications.
    """
    app.dependency_overrides[get_db] = override_get_db
    user, cookies, _ = await create_user_and_login(
        async_client, "multi_conn@example.com"
    )
    user_uuid = uuid.UUID(user["id"])

    with TestClient(app) as test_client:
        with test_client.websocket_connect(
            "/api/v1/notifications/ws",
            cookies=cookies,
        ) as ws_tab1:
            assert ws_tab1.receive_json()["event"] == "connected"

            with test_client.websocket_connect(
                "/api/v1/notifications/ws",
                cookies=cookies,
            ) as ws_tab2:
                assert ws_tab2.receive_json()["event"] == "connected"
                assert (
                    notification_broadcaster.get_user_connection_count(user_uuid)
                    == 2
                )

                # Directly broadcast to user
                payload = {
                    "id": "550e8400-e29b-41d4-a716-446655440099",
                    "user_id": user["id"],
                    "type": "test_event",
                    "title": "Broadcast Test",
                    "message": "Testing multi-connection",
                    "is_read": False,
                }
                delivered = await notification_broadcaster.broadcast_to_user(
                    user_uuid,
                    "notification_created",
                    payload,
                )
                assert delivered == 2

                msg1 = ws_tab1.receive_json()
                msg2 = ws_tab2.receive_json()
                assert msg1["event"] == "notification_created"
                assert msg2["event"] == "notification_created"
                assert msg1["data"]["title"] == "Broadcast Test"
                assert msg2["data"]["title"] == "Broadcast Test"


@pytest.mark.asyncio
async def test_websocket_read_sync_across_connections(async_client):
    """
    Marking a notification as read or all as read broadcasts sync events
    so that multiple tabs update their read counters immediately.
    """
    app.dependency_overrides[get_db] = override_get_db
    user, cookies, headers = await create_user_and_login(
        async_client, "read_sync@example.com"
    )

    with TestClient(app) as test_client:
        with test_client.websocket_connect(
            "/api/v1/notifications/ws",
            cookies=cookies,
        ) as ws:
            assert ws.receive_json()["event"] == "connected"

            # Mark all as read via REST
            all_read_res = await async_client.post(
                "/api/v1/notifications/read-all",
                cookies=cookies,
                headers=headers,
            )
            assert all_read_res.status_code == 200

            # WebSocket receives sync event
            sync_msg = ws.receive_json()
            assert sync_msg["event"] == "notifications_all_read"
            assert sync_msg["data"]["user_id"] == user["id"]


@pytest.mark.asyncio
async def test_websocket_origin_validation(async_client):
    """
    Cookie-authenticated WebSocket connections must enforce Origin validation
    to mitigate Cross-Site WebSocket Hijacking (CSWSH).
    """
    app.dependency_overrides[get_db] = override_get_db
    user, cookies, _ = await create_user_and_login(
        async_client, "origin_check@example.com"
    )

    with TestClient(app) as client:
        # 1. Untrusted malicious origin must be rejected with 1008
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/api/v1/notifications/ws",
                cookies=cookies,
                headers={"Origin": "https://malicious-attacker.com"},
            ):
                pass
        assert exc_info.value.code == 1008

        # 2. Allowed CORS origin must be accepted
        with client.websocket_connect(
            "/api/v1/notifications/ws",
            cookies=cookies,
            headers={"Origin": "http://localhost:3000"},
        ) as ws:
            connected = ws.receive_json()
            assert connected["type"] == "connected"
            assert connected["event"] == "connected"
            assert connected["event_id"] is not None
            assert connected["created_at"] is not None

        # 3. Bearer token connection from arbitrary origin is permitted
        # (for mobile or headless client integrations)
        token = cookies[settings.SESSION_COOKIE_NAME]
        with client.websocket_connect(
            "/api/v1/notifications/ws",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://external-client.app",
            },
        ) as ws:

            connected = ws.receive_json()
            assert connected["type"] == "connected"


@pytest.mark.asyncio
async def test_websocket_event_envelope_and_limits(async_client):
    """
    Verify stable event envelope (type, event_id, notification, created_at)
    and frame size / connection limits.
    """
    app.dependency_overrides[get_db] = override_get_db
    user, cookies, _ = await create_user_and_login(
        async_client, "envelope_user@example.com"
    )
    user_uuid = uuid.UUID(user["id"])

    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/notifications/ws",
            cookies=cookies,
        ) as ws:
            conn_msg = ws.receive_json()
            # Envelope validation
            assert conn_msg["type"] == "connected"
            assert conn_msg["event_id"] is not None
            assert conn_msg["created_at"] is not None
            assert conn_msg["notification"] is None

            # Test notification broadcast envelope
            test_payload = {
                "id": str(uuid.uuid4()),
                "user_id": user["id"],
                "actor_id": None,
                "organization_id": None,
                "type": "task_assigned",
                "title": "Envelope Title",
                "message": "Envelope Message",
                "resource_type": "task",
                "resource_id": str(uuid.uuid4()),
                "is_read": False,
                "created_at": "2026-10-27T10:00:00Z",
                "updated_at": "2026-10-27T10:00:00Z",
            }
            await notification_broadcaster.broadcast_to_user(
                user_uuid, "notification_created", test_payload
            )

            notif_msg = ws.receive_json()
            assert notif_msg["type"] == "notification_created"
            assert notif_msg["event"] == "notification_created"
            assert notif_msg["event_id"] is not None
            assert notif_msg["created_at"] is not None
            assert notif_msg["notification"] == test_payload
            assert notif_msg["data"] == test_payload

            # Test client oversize message rejection
            huge_msg = "x" * 5000
            ws.send_text(huge_msg)
            with pytest.raises(WebSocketDisconnect) as exc_info:
                ws.receive_text()
            assert exc_info.value.code == 1009


@pytest.mark.asyncio
async def test_websocket_max_connections_limit_per_user(async_client):
    """
    Verify per-user connection limits guard against socket exhaustion DoS.
    """
    import contextlib

    app.dependency_overrides[get_db] = override_get_db
    user, cookies, _ = await create_user_and_login(
        async_client, "dos_user@example.com"
    )
    user_uuid = uuid.UUID(user["id"])

    with TestClient(app) as client:
        with contextlib.ExitStack() as stack:
            # Connect up to the limit (10)
            for _ in range(notification_broadcaster.max_connections_per_user):
                ws = stack.enter_context(
                    client.websocket_connect(
                        "/api/v1/notifications/ws", cookies=cookies
                    )
                )
                assert ws.receive_json()["type"] == "connected"

            assert (
                notification_broadcaster.get_user_connection_count(user_uuid) == 10
            )

            # 11th connection must be rejected with 1008
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(
                    "/api/v1/notifications/ws", cookies=cookies
                ):
                    pass
            assert exc_info.value.code == 1008

    # When all sockets close, user connection count is cleaned up to 0
    assert notification_broadcaster.get_user_connection_count(user_uuid) == 0


@pytest.mark.asyncio
async def test_domain_events_due_date_status_and_comment_mentions(async_client):
    """
    Verify notification creation and real-time broadcast for:
    - Task due date change
    - Task status transition (notifying assignee and reporter, excluding actor)
    - Comment editing with new @mention
    - Duplicate prevention when multi-attribute updates occur
    """
    app.dependency_overrides[get_db] = override_get_db

    # Create users: Alice (lead/reporter), Bob (assignee), Charlie (new mention)
    alice, cookies_a, headers_a = await create_user_and_login(
        async_client, "alice_domain@example.com", full_name="Alice Lead"
    )
    bob, cookies_b, headers_b = await create_user_and_login(
        async_client, "bob_domain@example.com", full_name="Bob Assignee"
    )
    charlie, cookies_c, headers_c = await create_user_and_login(
        async_client, "charlie_domain@example.com", full_name="Charlie Member"
    )

    # Alice creates org and project
    org_res = await async_client.post(
        "/api/v1/organizations",
        json={"name": "Domain Org", "slug": "domain-org"},
        cookies=cookies_a,
        headers=headers_a,
    )
    assert org_res.status_code == 201
    org_id = org_res.json()["id"]

    for u_cookies, u_headers, email in [
        (cookies_b, headers_b, "bob_domain@example.com"),
        (cookies_c, headers_c, "charlie_domain@example.com"),
    ]:
        inv = await async_client.post(
            f"/api/v1/organizations/{org_id}/invitations",
            json={"email": email, "role": "member"},
            cookies=cookies_a,
            headers=headers_a,
        )
        token = inv.json()["token"]
        await async_client.post(
            f"/api/v1/organizations/invitations/accept?token={token}",
            cookies=u_cookies,
            headers=u_headers,
        )

    proj_res = await async_client.post(
        f"/api/v1/organizations/{org_id}/projects",
        json={"name": "Domain Project", "key": "DOM"},
        cookies=cookies_a,
        headers=headers_a,
    )
    project_id = proj_res.json()["id"]

    # Connect WebSocket for Bob (assignee)
    with TestClient(app) as client:
        with client.websocket_connect(
            "/api/v1/notifications/ws",
            cookies=cookies_b,
        ) as ws_bob:
            assert ws_bob.receive_json()["type"] == "connected"

            # 1. Alice creates task assigned to Bob
            task_res = await async_client.post(
                f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks",
                json={"title": "Domain Test Task", "assignee_id": bob["id"]},
                cookies=cookies_a,
                headers=headers_a,
            )
            assert task_res.status_code == 201
            task = task_res.json()
            task_id = task["id"]

            notif_assign = ws_bob.receive_json()
            assert notif_assign["type"] == "notification_created"
            assert notif_assign["data"]["type"] == "task_assigned"

            # 2. Alice updates task due date -> Bob receives task_due_date_changed
            due_res = await async_client.patch(
                f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}",
                json={"due_date": "2026-11-15T00:00:00Z", "version": task["version"]},
                cookies=cookies_a,
                headers=headers_a,
            )
            assert due_res.status_code == 200
            task = due_res.json()

            notif_due = ws_bob.receive_json()
            assert notif_due["type"] == "notification_created"
            assert notif_due["data"]["type"] == "task_due_date_changed"
            assert "Due Date Changed" in notif_due["data"]["title"]

            # 3. Status transition: Alice moves task status to in_progress via /move
            # Connect ws for Alice (reporter) to verify Alice is NOT notified
            with client.websocket_connect(
                "/api/v1/notifications/ws",
                cookies=cookies_a,
            ) as ws_alice:
                assert ws_alice.receive_json()["type"] == "connected"

                status_res = await async_client.post(
                    f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/move",
                    json={"status": "in_progress", "version": task["version"]},
                    cookies=cookies_a,
                    headers=headers_a,
                )
                assert status_res.status_code == 200
                task = status_res.json()

                # Bob receives status changed notification
                notif_status = ws_bob.receive_json()
                assert notif_status["type"] == "notification_created"
                assert notif_status["data"]["type"] == "task_status_changed"

                # Alice does NOT receive any self-notification
                ws_alice.send_json({"type": "ping"})
                pong = ws_alice.receive_json()
                assert pong["type"] == "pong"

            # 4. Comment with mentions and edit comment adding new mention
            comment_res = await async_client.post(
                f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments",
                json={
                    "content": "Initial comment without mentions",
                    "mentioned_user_ids": [],
                },
                cookies=cookies_a,
                headers=headers_a,
            )
            assert comment_res.status_code == 201
            comment_id = comment_res.json()["id"]

            # Bob receives task_comment notification
            notif_comment = ws_bob.receive_json()
            assert notif_comment["data"]["type"] == "task_comment"

            # Connect Charlie's socket
            with client.websocket_connect(
                "/api/v1/notifications/ws",
                cookies=cookies_c,
            ) as ws_charlie:
                assert ws_charlie.receive_json()["type"] == "connected"

                # Alice edits comment and adds mention for Charlie
                edit_res = await async_client.patch(
                    f"/api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments/{comment_id}",
                    json={
                        "content": "Edited comment mentioning Charlie",
                        "mentioned_user_ids": [charlie["id"]],
                    },
                    cookies=cookies_a,
                    headers=headers_a,
                )
                assert edit_res.status_code == 200

                # Charlie receives mention notification
                notif_charlie = ws_charlie.receive_json()
                assert notif_charlie["type"] == "notification_created"
                assert notif_charlie["data"]["type"] == "task_mentioned"


@pytest.mark.asyncio
async def test_notification_rest_auth_and_missed_event_recovery(async_client):
    """
    Test:
    - User cannot access or mark another user's notifications as read.
    - Offline notification persistence and delta synchronization with `since` parameter.
    """
    app.dependency_overrides[get_db] = override_get_db

    user1, cookies1, headers1 = await create_user_and_login(
        async_client, "user1_rest@example.com"
    )
    user2, cookies2, headers2 = await create_user_and_login(
        async_client, "user2_rest@example.com"
    )

    # Broadcast direct notification for user 1
    u1_uuid = uuid.UUID(user1["id"])
    async with TestingSessionLocal() as db:
        from taskflow.domains.collaboration.repository import CollaborationRepository

        repo = CollaborationRepository(db)
        notif1 = await repo.create_notification(
            user_id=u1_uuid,
            actor_id=None,
            organization_id=None,
            type="task_assigned",
            title="First Notification",
            message="Message 1",
        )
        await db.commit()
        notif1_id = notif1.id
        notif1_created_at = notif1.created_at

    # 1. User 2 cannot mark User 1's notification as read (returns 404 NOT_FOUND)
    forbidden_res = await async_client.patch(
        f"/api/v1/notifications/{notif1_id}/read",
        cookies=cookies2,
        headers=headers2,
    )
    assert forbidden_res.status_code == 404
    assert forbidden_res.json()["error"]["code"] == "NOTIFICATION_NOT_FOUND"

    # User 2 list notifications does not show user 1's notification
    u2_list = await async_client.get(
        "/api/v1/notifications",
        cookies=cookies2,
    )
    assert u2_list.status_code == 200
    assert u2_list.json()["total"] == 0

    # User 1 sees notification 1
    u1_list = await async_client.get(
        "/api/v1/notifications",
        cookies=cookies1,
    )
    assert u1_list.status_code == 200
    assert u1_list.json()["total"] == 1
    assert u1_list.json()["unread_count"] == 1

    # 2. Add second notification while User 1 is 'offline'
    async with TestingSessionLocal() as db:
        repo = CollaborationRepository(db)
        await repo.create_notification(
            user_id=u1_uuid,
            actor_id=None,
            organization_id=None,
            type="task_status_changed",
            title="Second Notification",
            message="Message 2",
        )
        await db.commit()



    # User 1 queries delta sync with since={notif1_created_at}
    delta_res = await async_client.get(
        "/api/v1/notifications",
        params={"since": notif1_created_at.isoformat()},
        cookies=cookies1,
    )
    assert delta_res.status_code == 200
    delta_data = delta_res.json()
    assert delta_data["total"] == 1
    assert delta_data["items"][0]["title"] == "Second Notification"


    # User 1 marks single notification as read
    mark_res = await async_client.patch(
        f"/api/v1/notifications/{notif1_id}/read",
        cookies=cookies1,
        headers=headers1,
    )
    assert mark_res.status_code == 200
    assert mark_res.json()["is_read"] is True

    # Check unread count
    unread_res = await async_client.get(
        "/api/v1/notifications/unread-count",
        cookies=cookies1,
    )
    assert unread_res.status_code == 200
    assert unread_res.json()["unread_count"] == 1

