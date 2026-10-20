import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserBrief(BaseModel):
    """Compact user profile for comments, actors, and mentions."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique user identifier")
    email: str = Field(..., description="User email address")
    full_name: str = Field(..., description="User full display name")
    avatar_url: str | None = Field(default=None, description="Optional avatar URL")


class MentionableUserResponse(BaseModel):
    """User representation for frontend @-mention autocomplete."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="User identifier")
    email: str = Field(..., description="User email address")
    full_name: str = Field(..., description="User full display name")
    role: str | None = Field(default=None, description="Project or organization role")


class CommentMentionResponse(BaseModel):
    """User mention record attached to a comment."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Mention identifier")
    comment_id: uuid.UUID = Field(..., description="Associated comment identifier")
    user_id: uuid.UUID = Field(..., description="Mentioned user identifier")
    created_at: datetime = Field(..., description="Mention creation timestamp (UTC)")
    user: UserBrief | None = Field(default=None, description="Mentioned user details")


class WorkItemCommentResponse(BaseModel):
    """Task discussion comment with author and structured mentions."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique comment identifier")
    work_item_id: uuid.UUID = Field(..., description="Associated work item identifier")
    author_id: uuid.UUID | None = Field(
        default=None, description="Author user identifier"
    )
    content: str = Field(..., description="Comment Markdown content")
    is_edited: bool = Field(
        default=False, description="Whether comment has been modified"
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")
    author: UserBrief | None = Field(default=None, description="Author user details")
    mentions: list[CommentMentionResponse] = Field(
        default_factory=list, description="Users mentioned in comment"
    )


class WorkItemCommentListResponse(BaseModel):
    """Paginated list of task comments."""

    items: list[WorkItemCommentResponse] = Field(..., description="List of comments")
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Items per page")
    total: int = Field(..., description="Total comment count")


class CommentCreateRequest(BaseModel):
    """Payload to add a new comment to a work item."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Comment markdown content",
        examples=["Reviewed the PR and verified the test suite passes."],
    )
    mentioned_user_ids: list[uuid.UUID] = Field(
        default_factory=list,
        description="Structured list of user IDs mentioned in the comment",
    )


class CommentUpdateRequest(BaseModel):
    """Payload to modify an existing comment."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Updated comment content",
    )
    mentioned_user_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Optional updated list of mentioned user IDs",
    )


class NotificationResponse(BaseModel):
    """User in-app notification."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique notification identifier")
    user_id: uuid.UUID = Field(..., description="Recipient user identifier")
    actor_id: uuid.UUID | None = Field(
        default=None, description="Acting user identifier"
    )
    organization_id: uuid.UUID | None = Field(
        default=None, description="Organization ID"
    )
    type: str = Field(
        ..., description="Notification event type (e.g. task_assigned, task_mentioned)"
    )
    title: str = Field(..., description="Notification headline")
    message: str = Field(..., description="Notification body content")
    resource_type: str | None = Field(
        default=None, description="Referenced entity type (task, comment)"
    )
    resource_id: uuid.UUID | None = Field(
        default=None, description="Referenced entity ID"
    )
    is_read: bool = Field(
        default=False, description="Whether notification has been marked read"
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")
    actor: UserBrief | None = Field(
        default=None, description="User who triggered event"
    )


class NotificationListResponse(BaseModel):
    """Paginated list of user notifications with unread count."""

    items: list[NotificationResponse] = Field(..., description="List of notifications")
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Items per page")
    total: int = Field(..., description="Total notifications")
    unread_count: int = Field(default=0, description="Total unread notifications count")


class NotificationUnreadCountResponse(BaseModel):
    """Quick count of unread notifications for badge counters."""

    unread_count: int = Field(..., description="Total unread notifications count")
