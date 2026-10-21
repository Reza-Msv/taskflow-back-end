import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from taskflow.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from taskflow.domains.collaboration.models import WorkItemComment
    from taskflow.domains.identity.models import User
    from taskflow.domains.organizations.models import Organization
    from taskflow.domains.projects.models import Project
    from taskflow.domains.scrum.models import Sprint

# Association table for Many-to-Many WorkItem <-> Label relationship
work_item_labels = Table(
    "work_item_labels",
    Base.metadata,
    Column(
        "work_item_id",
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "label_id",
        UUID(as_uuid=True),
        ForeignKey("labels.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Label(Base, UUIDMixin, TimestampMixin):
    """Organization-scoped taxonomy label for categorizing work items."""

    __tablename__ = "labels"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    color: Mapped[str] = mapped_column(
        String(20),
        default="#6B7280",
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "name",
            name="uq_labels_organization_name",
        ),
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
    )
    work_items: Mapped[list["WorkItem"]] = relationship(
        "WorkItem",
        secondary=work_item_labels,
        back_populates="labels",
    )


class WorkflowStatus(Base, UUIDMixin, TimestampMixin):
    """Workflow state configured globally or per project."""

    __tablename__ = "workflow_statuses"

    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(50),
        default="todo",
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    project: Mapped["Project | None"] = relationship(
        "Project",
    )


class WorkflowTransition(Base, UUIDMixin):
    """Allowed transition rule between workflow statuses."""

    __tablename__ = "workflow_transitions"

    from_status_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_statuses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    to_status_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workflow_statuses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class WorkItem(Base, UUIDMixin, TimestampMixin):
    """
    Polymorphic core work unit (task, bug, story, epic, subtask)
    enforcing tenant isolation and optimistic concurrency.
    """

    __tablename__ = "work_items"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    issue_key: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(
        String(50),
        default="task",
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="todo",
        nullable=False,
        index=True,
    )
    priority: Mapped[str] = mapped_column(
        String(50),
        default="medium",
        nullable=False,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    sprint_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sprints.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    rank: Mapped[str] = mapped_column(
        String(255),
        default="0|hzzzzz:",
        nullable=False,
        index=True,
    )
    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    estimated_points: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "sequence_number",
            name="uq_work_items_project_sequence",
        ),
        UniqueConstraint(
            "project_id",
            "issue_key",
            name="uq_work_items_project_issue_key",
        ),
        Index("ix_work_items_org_status", "organization_id", "status"),
    )

    project: Mapped["Project"] = relationship(
        "Project",
    )
    organization: Mapped["Organization"] = relationship(
        "Organization",
    )
    parent: Mapped["WorkItem | None"] = relationship(
        "WorkItem",
        remote_side="WorkItem.id",
        back_populates="children",
    )
    children: Mapped[list["WorkItem"]] = relationship(
        "WorkItem",
        back_populates="parent",
    )
    reporter: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[reporter_id],
    )
    assignee: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[assignee_id],
    )
    sprint: Mapped["Sprint | None"] = relationship(
        "Sprint",
        foreign_keys=[sprint_id],
        back_populates="work_items",
    )
    labels: Mapped[list["Label"]] = relationship(
        "Label",
        secondary=work_item_labels,
        back_populates="work_items",
    )
    activities: Mapped[list["WorkItemActivity"]] = relationship(
        "WorkItemActivity",
        back_populates="work_item",
        cascade="all, delete-orphan",
    )
    attachments: Mapped[list["Attachment"]] = relationship(
        "Attachment",
        back_populates="work_item",
        cascade="all, delete-orphan",
    )
    comments: Mapped[list["WorkItemComment"]] = relationship(
        "WorkItemComment",
        back_populates="work_item",
        cascade="all, delete-orphan",
    )
    checklists: Mapped[list["ChecklistItem"]] = relationship(
        "ChecklistItem",
        back_populates="work_item",
        cascade="all, delete-orphan",
        order_by="ChecklistItem.position",
    )


class WorkItemActivity(Base, UUIDMixin):
    """Audit activity stream tracking granular property modifications."""

    __tablename__ = "work_item_activities"

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    field: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    old_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    new_value: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="activities",
    )
    user: Mapped["User | None"] = relationship(
        "User",
    )


class Attachment(Base, UUIDMixin):
    """File attachment metadata associated with a work item."""

    __tablename__ = "attachments"

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    uploader_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    file_size: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    content_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    storage_path: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="attachments",
    )
    uploader: Mapped["User | None"] = relationship(
        "User",
    )


class ChecklistItem(Base, UUIDMixin, TimestampMixin):
    """Checklist item under a work item with completion and ordering."""

    __tablename__ = "checklist_items"

    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    position: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    work_item: Mapped["WorkItem"] = relationship(
        "WorkItem",
        back_populates="checklists",
    )
    assignee: Mapped["User | None"] = relationship(
        "User",
    )


class SavedFilter(Base, UUIDMixin, TimestampMixin):
    """User-owned persistent saved filter / view for work items."""

    __tablename__ = "saved_filters"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    description: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    filter_criteria: Mapped[dict] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    sort_by: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    sort_dir: Mapped[str | None] = mapped_column(
        String(10),
        nullable=True,
    )
    view_type: Mapped[str] = mapped_column(
        String(50),
        default="list",
        nullable=False,
    )
    is_favorite: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    __table_args__ = (
        Index("ix_saved_filters_org_user", "organization_id", "user_id"),
    )

    user: Mapped["User"] = relationship("User")
    organization: Mapped["Organization | None"] = relationship("Organization")
    project: Mapped["Project | None"] = relationship("Project")

