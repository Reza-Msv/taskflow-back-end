import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.exceptions import DomainException
from taskflow.domains.collaboration.models import (
    Notification,
    WorkItemComment,
)
from taskflow.domains.collaboration.repository import CollaborationRepository
from taskflow.domains.collaboration.schemas import (
    CommentCreateRequest,
    CommentMentionResponse,
    CommentUpdateRequest,
    MentionableUserResponse,
    NotificationListResponse,
    NotificationResponse,
    UserBrief,
    WorkItemCommentListResponse,
    WorkItemCommentResponse,
)
from taskflow.domains.identity.models import User
from taskflow.domains.organizations.models import OrganizationMembership
from taskflow.domains.organizations.repository import OrganizationRepository
from taskflow.domains.projects.models import ProjectMembership
from taskflow.domains.projects.repository import ProjectRepository
from taskflow.domains.work_items.models import WorkItem
from taskflow.domains.work_items.repository import WorkItemRepository

if TYPE_CHECKING:
    pass


from taskflow.domains.collaboration.websocket import (
    NotificationBroadcaster,
    get_notification_broadcaster,
)


class CollaborationService:
    """
    Business service orchestrating task comments, mentions, notifications,
    and mentionable member discovery.
    """

    def __init__(
        self,
        session: AsyncSession,
        broadcaster: NotificationBroadcaster | None = None,
    ) -> None:
        self.session = session
        self.collab_repo = CollaborationRepository(session)
        self.work_item_repo = WorkItemRepository(session)
        self.project_repo = ProjectRepository(session)
        self.org_repo = OrganizationRepository(session)
        self.broadcaster = broadcaster or get_notification_broadcaster()

    @staticmethod
    def _to_user_brief(user: User | None) -> UserBrief | None:
        if user is None:
            return None
        return UserBrief(
            id=user.id,
            email=user.email,
            full_name=user.full_name,
            avatar_url=getattr(user, "avatar_url", None),
        )

    def _to_comment_response(self, comment: WorkItemComment) -> WorkItemCommentResponse:
        author_dto = self._to_user_brief(comment.author)
        mentions_dtos = [
            CommentMentionResponse(
                id=m.id,
                comment_id=m.comment_id,
                user_id=m.user_id,
                created_at=m.created_at,
                user=self._to_user_brief(m.user),
            )
            for m in (comment.mentions or [])
        ]
        return WorkItemCommentResponse(
            id=comment.id,
            work_item_id=comment.work_item_id,
            author_id=comment.author_id,
            content=comment.content,
            is_edited=comment.is_edited,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            author=author_dto,
            mentions=mentions_dtos,
        )

    # -------------------------------------------------------------
    # Mention Validation & Autocomplete
    # -------------------------------------------------------------

    async def get_mentionable_members(
        self,
        organization_id: uuid.UUID,
        project_id: uuid.UUID,
        search: str | None = None,
    ) -> list[MentionableUserResponse]:
        """
        Return active users in the project/organization eligible for @-mentions.
        """
        project = await self.project_repo.get_by_id(project_id, org_id=organization_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Base active org members
        stmt = (
            select(User, OrganizationMembership.role)
            .join(OrganizationMembership, OrganizationMembership.user_id == User.id)
            .where(
                OrganizationMembership.organization_id == organization_id,
                OrganizationMembership.is_active == True,  # noqa: E712
                User.is_active == True,  # noqa: E712
            )
        )

        if project.is_private:
            # Must be project owner, project member, or org owner/admin
            proj_members_subquery = (
                select(ProjectMembership.user_id)
                .where(ProjectMembership.project_id == project_id)
                .scalar_subquery()
            )
            stmt = stmt.where(
                (User.id == project.owner_id)
                | (User.id.in_(proj_members_subquery))
                | (OrganizationMembership.role.in_(["owner", "admin"]))
            )

        if search:
            search_pattern = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                User.full_name.ilike(search_pattern) | User.email.ilike(search_pattern)
            )

        result = await self.session.execute(stmt)
        rows = result.all()

        return [
            MentionableUserResponse(
                id=u.id,
                email=u.email,
                full_name=u.full_name,
                role=role,
            )
            for u, role in rows
        ]

    async def validate_mentioned_users(
        self,
        task: WorkItem,
        mentioned_user_ids: list[uuid.UUID],
    ) -> list[uuid.UUID]:
        """
        Validate that mentioned user IDs belong to active, authorized members
        of the organization and project. Rejects unauthorized users.
        """
        if not mentioned_user_ids:
            return []

        # Deduplicate preserving order
        unique_ids = list(dict.fromkeys(mentioned_user_ids))

        # Check each user
        for uid in unique_ids:
            membership = await self.org_repo.get_membership(task.organization_id, uid)
            if membership is None or not membership.is_active:
                raise DomainException(
                    code="INVALID_MENTION",
                    message=(
                        "Mentioned user is not an active member of the organization."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"user_id": str(uid)},
                )

            # If project is private, verify project membership
            project = await self.project_repo.get_by_id(task.project_id)
            if project and project.is_private:
                is_authorized = project.owner_id == uid or membership.role in {
                    "owner",
                    "admin",
                }
                if not is_authorized:
                    proj_membership = await self.project_repo.get_membership(
                        task.project_id, uid
                    )
                    if proj_membership is None:
                        raise DomainException(
                            code="INVALID_MENTION",
                            message=(
                                "Mentioned user does not have permission to access "
                                "this private project."
                            ),
                            status_code=status.HTTP_400_BAD_REQUEST,
                            details={"user_id": str(uid)},
                        )

        return unique_ids

    # -------------------------------------------------------------
    # Comments CRUD
    # -------------------------------------------------------------

    async def create_comment(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: CommentCreateRequest,
    ) -> WorkItemCommentResponse:
        """
        Create a discussion comment, validate mentions, record activity,
        and dispatch notifications.
        """
        task = await self.work_item_repo.get_by_id(task_id, project_id=project_id)
        if task is None:
            raise DomainException(
                code="TASK_NOT_FOUND",
                message="Task not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Validate mentions
        validated_mention_ids = await self.validate_mentioned_users(
            task, request.mentioned_user_ids
        )

        # Create comment
        comment = await self.collab_repo.create_comment(
            work_item_id=task.id,
            author_id=actor_id,
            content=request.content.strip(),
        )

        # Persist mentions
        if validated_mention_ids:
            await self.collab_repo.set_comment_mentions(
                comment.id, validated_mention_ids
            )

        # Record activity
        snippet = (
            (comment.content[:80] + "...")
            if len(comment.content) > 80
            else comment.content
        )
        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=actor_id,
            field="comment_created",
            old_value=None,
            new_value=f"Added comment: {snippet}",
        )

        # Actor user for notification copy
        actor = await self.session.get(User, actor_id)
        actor_name = actor.full_name if actor else "A team member"

        # Dispatch notifications:
        # 1. Mention notifications (never notify the author themselves)
        created_notifications: list[Notification] = []
        notified_users: set[uuid.UUID] = {actor_id}
        for uid in validated_mention_ids:
            if uid not in notified_users:
                notified_users.add(uid)
                notif = await self.collab_repo.create_notification(
                    user_id=uid,
                    actor_id=actor_id,
                    organization_id=task.organization_id,
                    type="task_mentioned",
                    title=f"Mentioned in {task.issue_key}",
                    message=(
                        f"{actor_name} mentioned you in a comment on "
                        f"{task.issue_key}: {task.title}"
                    ),
                    resource_type="task",
                    resource_id=task.id,
                )
                created_notifications.append(notif)

        # 2. Notify assignee if not already notified
        if task.assignee_id and task.assignee_id not in notified_users:
            notified_users.add(task.assignee_id)
            notif = await self.collab_repo.create_notification(
                user_id=task.assignee_id,
                actor_id=actor_id,
                organization_id=task.organization_id,
                type="task_comment",
                title=f"New comment on {task.issue_key}",
                message=(
                    f"{actor_name} commented on task {task.issue_key}: {task.title}"
                ),
                resource_type="task",
                resource_id=task.id,
            )
            created_notifications.append(notif)

        # 3. Notify reporter if not already notified
        if task.reporter_id and task.reporter_id not in notified_users:
            notified_users.add(task.reporter_id)
            notif = await self.collab_repo.create_notification(
                user_id=task.reporter_id,
                actor_id=actor_id,
                organization_id=task.organization_id,
                type="task_comment",
                title=f"New comment on {task.issue_key}",
                message=(
                    f"{actor_name} commented on task {task.issue_key}: {task.title}"
                ),
                resource_type="task",
                resource_id=task.id,
            )
            created_notifications.append(notif)

        await self.session.commit()

        # Real-time WebSocket broadcast after database persistence
        for n in created_notifications:
            await self.broadcaster.broadcast_to_user(
                n.user_id,
                "notification_created",
                self._to_notification_payload(n),
            )

        # Reload with relations
        reloaded = await self.collab_repo.get_comment_by_id(comment.id)
        assert reloaded is not None
        return self._to_comment_response(reloaded)

    async def list_comments(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> WorkItemCommentListResponse:
        """List comments for a task in chronological order with pagination."""
        task = await self.work_item_repo.get_by_id(task_id, project_id=project_id)
        if task is None:
            raise DomainException(
                code="TASK_NOT_FOUND",
                message="Task not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        skip = (page - 1) * page_size
        comments, total = await self.collab_repo.list_comments(
            work_item_id=task.id,
            skip=skip,
            limit=page_size,
        )
        items = [self._to_comment_response(c) for c in comments]
        return WorkItemCommentListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
        )

    async def update_comment(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        comment_id: uuid.UUID,
        actor_id: uuid.UUID,
        is_admin_or_lead: bool,
        request: CommentUpdateRequest,
    ) -> WorkItemCommentResponse:
        """
        Update comment content and mentions.
        Allowed for: original author or project lead / org admin.
        """
        task = await self.work_item_repo.get_by_id(task_id, project_id=project_id)
        if task is None:
            raise DomainException(
                code="TASK_NOT_FOUND",
                message="Task not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        comment = await self.collab_repo.get_comment_by_id(
            comment_id, work_item_id=task.id
        )
        if comment is None:
            raise DomainException(
                code="COMMENT_NOT_FOUND",
                message="Comment not found or does not belong to this task.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if comment.author_id != actor_id and not is_admin_or_lead:
            raise DomainException(
                code="FORBIDDEN",
                message="You do not have permission to edit this comment.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        comment.content = request.content.strip()
        comment.is_edited = True

        new_notifications: list[Notification] = []
        if request.mentioned_user_ids is not None:
            existing_mention_ids = {m.user_id for m in (comment.mentions or [])}
            validated_ids = await self.validate_mentioned_users(
                task, request.mentioned_user_ids
            )
            await self.collab_repo.set_comment_mentions(comment.id, validated_ids)

            actor = await self.session.get(User, actor_id)
            actor_name = actor.full_name if actor else "A team member"

            for uid in validated_ids:
                if uid != actor_id and uid not in existing_mention_ids:
                    notif = await self.collab_repo.create_notification(
                        user_id=uid,
                        actor_id=actor_id,
                        organization_id=task.organization_id,
                        type="task_mentioned",
                        title=f"Mentioned in {task.issue_key}",
                        message=(
                            f"{actor_name} mentioned you in an updated comment on "
                            f"{task.issue_key}: {task.title}"
                        ),
                        resource_type="task",
                        resource_id=task.id,
                    )
                    new_notifications.append(notif)

        snippet = (
            (comment.content[:80] + "...")
            if len(comment.content) > 80
            else comment.content
        )
        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=actor_id,
            field="comment_edited",
            old_value=None,
            new_value=f"Edited comment: {snippet}",
        )

        await self.session.commit()

        # Real-time WebSocket delivery after successful database commit
        for n in new_notifications:
            await self.broadcaster.broadcast_to_user(
                n.user_id,
                "notification_created",
                self._to_notification_payload(n),
            )

        reloaded = await self.collab_repo.get_comment_by_id(comment.id)
        assert reloaded is not None
        return self._to_comment_response(reloaded)

    async def delete_comment(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        comment_id: uuid.UUID,
        actor_id: uuid.UUID,
        is_admin_or_lead: bool,
    ) -> None:
        """
        Delete comment.
        Allowed for: original author or project lead / org admin.
        """
        task = await self.work_item_repo.get_by_id(task_id, project_id=project_id)
        if task is None:
            raise DomainException(
                code="TASK_NOT_FOUND",
                message="Task not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        comment = await self.collab_repo.get_comment_by_id(
            comment_id, work_item_id=task.id
        )
        if comment is None:
            raise DomainException(
                code="COMMENT_NOT_FOUND",
                message="Comment not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if comment.author_id != actor_id and not is_admin_or_lead:
            raise DomainException(
                code="FORBIDDEN",
                message="You do not have permission to delete this comment.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=actor_id,
            field="comment_deleted",
            old_value=f"Comment by {comment.author_id}",
            new_value=None,
        )

        await self.collab_repo.delete_comment(comment)
        await self.session.commit()

    # -------------------------------------------------------------
    # Notifications Lifecycle
    # -------------------------------------------------------------

    def _to_notification_response(self, notif: Notification) -> NotificationResponse:
        return NotificationResponse(
            id=notif.id,
            user_id=notif.user_id,
            actor_id=notif.actor_id,
            organization_id=notif.organization_id,
            type=notif.type,
            title=notif.title,
            message=notif.message,
            resource_type=notif.resource_type,
            resource_id=notif.resource_id,
            is_read=notif.is_read,
            created_at=notif.created_at,
            updated_at=notif.updated_at,
            actor=self._to_user_brief(notif.actor),
        )

    def _to_notification_payload(self, notif: Notification) -> dict[str, Any]:
        actor_brief = self._to_user_brief(notif.actor)
        return {
            "id": str(notif.id),
            "user_id": str(notif.user_id),
            "actor_id": str(notif.actor_id) if notif.actor_id else None,
            "organization_id": (
                str(notif.organization_id) if notif.organization_id else None
            ),
            "type": notif.type,
            "title": notif.title,
            "message": notif.message,
            "resource_type": notif.resource_type,
            "resource_id": str(notif.resource_id) if notif.resource_id else None,
            "is_read": notif.is_read,
            "created_at": notif.created_at.isoformat(),
            "updated_at": notif.updated_at.isoformat(),
            "actor": (actor_brief.model_dump(mode="json") if actor_brief else None),
        }

    async def list_notifications(
        self,
        user_id: uuid.UUID,
        unread_only: bool = False,
        page: int = 1,
        page_size: int = 25,
        since: datetime | None = None,
    ) -> NotificationListResponse:
        """Fetch paginated notifications for current user."""
        skip = (page - 1) * page_size
        notifications, total = await self.collab_repo.list_notifications(
            user_id=user_id,
            unread_only=unread_only,
            since=since,
            skip=skip,
            limit=page_size,
        )
        unread_count = await self.collab_repo.get_unread_count(user_id)
        items = [self._to_notification_response(n) for n in notifications]
        return NotificationListResponse(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            unread_count=unread_count,
        )

    async def get_unread_notification_count(self, user_id: uuid.UUID) -> int:
        """Get count of unread notifications for quick UI badges."""
        return await self.collab_repo.get_unread_count(user_id)

    async def mark_notification_read(
        self,
        notification_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> NotificationResponse:
        """Mark single notification as read."""
        notif = await self.collab_repo.mark_notification_read(
            notification_id=notification_id,
            user_id=user_id,
        )
        if notif is None:
            raise DomainException(
                code="NOTIFICATION_NOT_FOUND",
                message="Notification not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        await self.session.commit()
        await self.broadcaster.broadcast_to_user(
            user_id,
            "notification_read",
            {
                "id": str(notification_id),
                "user_id": str(user_id),
                "is_read": True,
            },
        )
        return self._to_notification_response(notif)

    async def mark_all_notifications_read(self, user_id: uuid.UUID) -> int:
        """Mark all unread notifications as read for current user."""
        count = await self.collab_repo.mark_all_notifications_read(user_id)
        await self.session.commit()
        await self.broadcaster.broadcast_to_user(
            user_id,
            "notifications_all_read",
            {
                "user_id": str(user_id),
                "marked_count": count,
            },
        )
        return count
