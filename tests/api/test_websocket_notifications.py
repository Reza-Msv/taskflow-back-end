import uuid

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests.conftest import override_get_db

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
