import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from taskflow.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from taskflow.domains.identity.models import User
    from taskflow.domains.work_items.models import WorkItem


class WorkItemComment(Base, UUIDMixin, TimestampMixin):
    """Discussion comment on a work item."""

    __tablename__ = "work_item_comments"

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_edited: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="comments",
    )
    author: Mapped["User | None"] = relationship(
        "User",
    )


class Notification(Base, UUIDMixin, TimestampMixin):
    """In-app user notification for events, assignments, and mentions."""

    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    message: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    resource_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    resource_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
        index=True,
    )

    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
    )
    actor: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[actor_id],
    )
