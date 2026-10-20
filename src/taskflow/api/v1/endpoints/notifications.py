import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from taskflow.api.deps import get_collaboration_service, get_current_active_user
from taskflow.domains.collaboration.schemas import (
    NotificationListResponse,
    NotificationResponse,
    NotificationUnreadCountResponse,
)
from taskflow.domains.collaboration.service import CollaborationService
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import MessageResponse

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
) -> NotificationListResponse:
    """Retrieve paginated in-app notifications for the authenticated user."""
    return await collab_service.list_notifications(
        user_id=current_user.id,
        unread_only=unread_only,
        page=page,
        page_size=page_size,
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
