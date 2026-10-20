import uuid
from collections.abc import Sequence

from sqlalchemy import and_, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.collaboration.models import (
    CommentMention,
    Notification,
    WorkItemComment,
)


class CollaborationRepository:
    """Persistence operations for WorkItemComment, CommentMention, and Notification."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------
    # Comments
    # -------------------------------------------------------------

    async def create_comment(
        self,
        work_item_id: uuid.UUID,
        author_id: uuid.UUID | None,
        content: str,
    ) -> WorkItemComment:
        """Create a comment on a work item."""
        comment = WorkItemComment(
            work_item_id=work_item_id,
            author_id=author_id,
            content=content,
            is_edited=False,
        )
        self.session.add(comment)
        await self.session.flush()
        return comment

    async def get_comment_by_id(
        self,
        comment_id: uuid.UUID,
        work_item_id: uuid.UUID | None = None,
    ) -> WorkItemComment | None:
        """Fetch a single comment with author and mentions eagerly loaded."""
        stmt = (
            select(WorkItemComment)
            .options(
                selectinload(WorkItemComment.author),
                selectinload(WorkItemComment.mentions).selectinload(
                    CommentMention.user
                ),
            )
            .where(WorkItemComment.id == comment_id)
        )
        if work_item_id is not None:
            stmt = stmt.where(WorkItemComment.work_item_id == work_item_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_comments(
        self,
        work_item_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
    ) -> tuple[list[WorkItemComment], int]:
        """Fetch paginated comments for a work item in ascending creation order."""
        count_stmt = select(func.count(WorkItemComment.id)).where(
            WorkItemComment.work_item_id == work_item_id
        )
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(WorkItemComment)
            .options(
                selectinload(WorkItemComment.author),
                selectinload(WorkItemComment.mentions).selectinload(
                    CommentMention.user
                ),
            )
            .where(WorkItemComment.work_item_id == work_item_id)
            .order_by(WorkItemComment.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def delete_comment(self, comment: WorkItemComment) -> None:
        """Delete a comment entity."""
        await self.session.delete(comment)
        await self.session.flush()

    # -------------------------------------------------------------
    # Mentions
    # -------------------------------------------------------------

    async def set_comment_mentions(
        self,
        comment_id: uuid.UUID,
        user_ids: Sequence[uuid.UUID],
    ) -> list[CommentMention]:
        """
        Replace mentions for a comment, ensuring no duplicates.
        """
        # Delete existing mentions
        existing_stmt = select(CommentMention).where(
            CommentMention.comment_id == comment_id
        )
        existing = (await self.session.execute(existing_stmt)).scalars().all()
        for m in existing:
            await self.session.delete(m)
        await self.session.flush()

        # Deduplicate user_ids preserving order
        seen = set()
        unique_ids = []
        for uid in user_ids:
            if uid not in seen:
                seen.add(uid)
                unique_ids.append(uid)

        new_mentions = []
        for uid in unique_ids:
            mention = CommentMention(comment_id=comment_id, user_id=uid)
            self.session.add(mention)
            new_mentions.append(mention)

        await self.session.flush()
        return new_mentions

    # -------------------------------------------------------------
    # Notifications
    # -------------------------------------------------------------

    async def create_notification(
        self,
        user_id: uuid.UUID,
        actor_id: uuid.UUID | None,
        organization_id: uuid.UUID | None,
        type: str,
        title: str,
        message: str,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
    ) -> Notification:
        """Create a notification record for a user."""
        notification = Notification(
            user_id=user_id,
            actor_id=actor_id,
            organization_id=organization_id,
            type=type,
            title=title,
            message=message,
            resource_type=resource_type,
            resource_id=resource_id,
            is_read=False,
        )
        self.session.add(notification)
        await self.session.flush()
        return notification

    async def get_notification_by_id(
        self,
        notification_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
    ) -> Notification | None:
        """Fetch notification by ID, optionally verifying ownership."""
        stmt = (
            select(Notification)
            .options(selectinload(Notification.actor))
            .where(Notification.id == notification_id)
        )
        if user_id is not None:
            stmt = stmt.where(Notification.user_id == user_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_notifications(
        self,
        user_id: uuid.UUID,
        unread_only: bool = False,
        skip: int = 0,
        limit: int = 25,
    ) -> tuple[list[Notification], int]:
        """Fetch paginated notifications for user, newest first."""
        base_filter = [Notification.user_id == user_id]
        if unread_only:
            base_filter.append(Notification.is_read == False)  # noqa: E712

        count_stmt = select(func.count(Notification.id)).where(and_(*base_filter))
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(Notification)
            .options(selectinload(Notification.actor))
            .where(and_(*base_filter))
            .order_by(Notification.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def get_unread_count(self, user_id: uuid.UUID) -> int:
        """Return total unread notifications for user."""
        stmt = select(func.count(Notification.id)).where(
            Notification.user_id == user_id,
            Notification.is_read == False,  # noqa: E712
        )
        return (await self.session.execute(stmt)).scalar() or 0

    async def mark_notification_read(
        self,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Notification | None:
        """Mark single notification as read if owned by user."""
        notification = await self.get_notification_by_id(
            notification_id, user_id=user_id
        )
        if notification:
            notification.is_read = True
            await self.session.flush()
        return notification

    async def mark_all_notifications_read(self, user_id: uuid.UUID) -> int:
        """Mark all unread notifications as read for a user."""
        stmt = (
            update(Notification)
            .where(
                Notification.user_id == user_id,
                Notification.is_read == False,  # noqa: E712
            )
            .values(is_read=True)
        )
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(getattr(result, "rowcount", 0))
