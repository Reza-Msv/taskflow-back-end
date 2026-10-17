import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from taskflow.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from taskflow.domains.projects.models import Project


class Sprint(Base, UUIDMixin, TimestampMixin):
    """Scrum sprint cycle belonging to a project."""

    __tablename__ = "sprints"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    goal: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        default="draft",
        nullable=False,
    )
    start_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    end_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_sprints_project_status", "project_id", "status"),
    )

    project: Mapped["Project"] = relationship(
        "Project",
    )
    snapshots: Mapped[list["SprintSnapshot"]] = relationship(
        "SprintSnapshot",
        back_populates="sprint",
        cascade="all, delete-orphan",
    )
    retrospective: Mapped["SprintRetrospective | None"] = relationship(
        "SprintRetrospective",
        back_populates="sprint",
        uselist=False,
        cascade="all, delete-orphan",
    )


class SprintSnapshot(Base, UUIDMixin):
    """Scope snapshot recorded upon sprint lifecycle milestones."""

    __tablename__ = "sprint_snapshots"

    sprint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sprints.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    snapshot_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    total_points: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    completed_points: Mapped[float] = mapped_column(
        Float,
        default=0.0,
        nullable=False,
    )
    total_items: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    completed_items: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    scope_added_items: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    scope_removed_items: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )

    sprint: Mapped["Sprint"] = relationship(
        "Sprint",
        back_populates="snapshots",
    )


class SprintRetrospective(Base, UUIDMixin, TimestampMixin):
    """Sprint retrospective review and action items."""

    __tablename__ = "sprint_retrospectives"

    sprint_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sprints.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    what_went_well: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    what_could_be_improved: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    action_items: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    sprint: Mapped["Sprint"] = relationship(
        "Sprint",
        back_populates="retrospective",
    )
