import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.api.deps import (
    authenticate_websocket,
    get_collaboration_service,
    get_current_active_user,
    get_db,
)
from taskflow.domains.collaboration.schemas import (
    NotificationListResponse,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from taskflow.domains.collaboration.service import CollaborationService
from taskflow.domains.collaboration.websocket import (
    InMemoryConnectionManager,
    get_notification_broadcaster,
)
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import MessageResponse

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
)


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="List User Notifications",
)
async def list_notifications(
    current_user: Annotated[User, Depends(get_current_active_user)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    unread_only: bool = Query(False, description="Filter only unread notifications"),
    since: datetime | None = Query(
        None,
        description=(
            "Filter notifications created after this UTC timestamp for "
            "delta synchronization"
        ),
    ),

) -> NotificationListResponse:
    """Retrieve paginated in-app notifications for the authenticated user."""
    return await collab_service.list_notifications(
        user_id=current_user.id,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
        since=since,
    )


@router.get(
    "/unread-count",
    response_model=NotificationUnreadCountResponse,
    summary="Get Unread Notification Count",
)
async def get_unread_count(
    current_user: Annotated[User, Depends(get_current_active_user)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
) -> NotificationUnreadCountResponse:
    """Get the quick count of unread notifications for navbar badges."""
    count = await collab_service.get_unread_notification_count(user_id=current_user.id)
    return NotificationUnreadCountResponse(unread_count=count)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark Notification as Read",
)
async def mark_notification_read(
    notification_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
) -> NotificationResponse:
    """Mark an individual notification as read."""
    return await collab_service.mark_notification_read(
        notification_id=notification_id,
        user_id=current_user.id,
    )


@router.post(
    "/read-all",
    response_model=MessageResponse,
    summary="Mark All Notifications as Read",
)
async def mark_all_notifications_read(
    current_user: Annotated[User, Depends(get_current_active_user)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
) -> MessageResponse:
    """Mark all unread notifications as read for the authenticated user."""
    count = await collab_service.mark_all_notifications_read(user_id=current_user.id)
    return MessageResponse(message=f"Marked {count} notifications as read.")


MAX_CLIENT_FRAME_BYTES: int = 4096


@router.websocket("/ws")
async def websocket_notifications(
    websocket: WebSocket,
    session: Annotated[AsyncSession, Depends(get_db)],
    broadcaster: Annotated[
        InMemoryConnectionManager, Depends(get_notification_broadcaster)
    ],
) -> None:
    """
    Authenticated real-time WebSocket connection for user notifications.
    Authenticates using session cookie or Authorization Bearer header.
    Rejects unauthenticated connections with WS_1008_POLICY_VIOLATION.
    """
    user = await authenticate_websocket(websocket, session)
    if user is None:
        return

    connected = await broadcaster.connect(user.id, websocket)
    if not connected:
        return

    try:
        # Confirm connection with initial handshake event adhering to envelope
        conn_time = datetime.now(UTC).isoformat()
        await websocket.send_json(
            {
                "type": "connected",
                "event": "connected",
                "event_id": str(uuid.uuid4()),
                "notification": None,
                "data": {
                    "user_id": str(user.id),
                    "timestamp": conn_time,
                },
                "created_at": conn_time,
                "timestamp": conn_time,
            }
        )
        while True:
            raw = await websocket.receive_text()
            if len(raw.encode("utf-8")) > MAX_CLIENT_FRAME_BYTES:
                logger.warning(
                    "WebSocket message from user %s exceeded max frame size (%d bytes)",
                    user.id,
                    MAX_CLIENT_FRAME_BYTES,
                )
                await websocket.close(
                    code=1009,
                    reason="Message too large.",
                )
                break


            try:
                msg = json.loads(raw)
                if isinstance(msg, dict) and (
                    msg.get("event") == "ping" or msg.get("type") == "ping"
                ):
                    pong_time = datetime.now(UTC).isoformat()
                    await websocket.send_json(
                        {
                            "type": "pong",
                            "event": "pong",
                            "event_id": str(uuid.uuid4()),
                            "notification": None,
                            "data": {"timestamp": pong_time},
                            "created_at": pong_time,
                            "timestamp": pong_time,
                        }
                    )
            except Exception:
                pass
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected for user %s", user.id)
    except Exception as exc:
        logger.debug("WebSocket error for user %s: %s", user.id, exc)
    finally:
        await broadcaster.disconnect(user.id, websocket)


