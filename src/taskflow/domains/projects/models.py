import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from taskflow.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from taskflow.domains.identity.models import User
    from taskflow.domains.organizations.models import Organization


class Project(Base, UUIDMixin, TimestampMixin):
    """Project entity belonging to an organization."""

    __tablename__ = "projects"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    is_private: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    is_archived: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "key",
            name="uq_projects_organization_key",
        ),
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
    )
    owner: Mapped["User | None"] = relationship(
        "User",
    )
    memberships: Mapped[list["ProjectMembership"]] = relationship(
        "ProjectMembership",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    sequence: Mapped["ProjectSequence | None"] = relationship(
        "ProjectSequence",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
    )


class ProjectMembership(Base, UUIDMixin, TimestampMixin):
    """User membership and authorization role on a specific project."""

    __tablename__ = "project_memberships"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(50),
        default="member",
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "user_id",
            name="uq_project_memberships_project_user",
        ),
    )

    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="memberships",
    )
    user: Mapped["User"] = relationship(
        "User",
    )


class ProjectSequence(Base, UUIDMixin):
    """
    Transaction-safe project issue sequence counter.
    Guarantees collision-free, gapless issue keys (e.g., TF-101)
    under concurrent writes.
    """

    __tablename__ = "project_sequences"

    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )
    current_sequence: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    project: Mapped["Project"] = relationship(
        "Project",
        back_populates="sequence",
    )
