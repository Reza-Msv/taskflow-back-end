import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from taskflow.db.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from taskflow.domains.identity.models import User


class Organization(Base, UUIDMixin, TimestampMixin):
    """Tenant organization entity."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    slug: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    memberships: Mapped[list["OrganizationMembership"]] = relationship(
        "OrganizationMembership",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    invitations: Mapped[list["Invitation"]] = relationship(
        "Invitation",
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    teams: Mapped[list["Team"]] = relationship(
        "Team",
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class OrganizationMembership(Base, UUIDMixin, TimestampMixin):
    """User membership within an organization with role assignment."""

    __tablename__ = "organization_memberships"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
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
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_memberships_org_user",
        ),
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="memberships",
    )
    user: Mapped["User"] = relationship(
        "User",
    )


class Invitation(Base, UUIDMixin, TimestampMixin):
    """Organization membership invitation with single-use token."""

    __tablename__ = "invitations"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        String(50),
        default="member",
        nullable=False,
    )
    token: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="invitations",
    )
    invited_by: Mapped["User | None"] = relationship(
        "User",
    )


class Team(Base, UUIDMixin, TimestampMixin):
    """Functional team within an organization."""

    __tablename__ = "teams"

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
    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    organization: Mapped["Organization"] = relationship(
        "Organization",
        back_populates="teams",
    )
    memberships: Mapped[list["TeamMembership"]] = relationship(
        "TeamMembership",
        back_populates="team",
        cascade="all, delete-orphan",
    )


class TeamMembership(Base, UUIDMixin, TimestampMixin):
    """Association of a user with an organizational team."""

    __tablename__ = "team_memberships"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
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
            "team_id",
            "user_id",
            name="uq_team_memberships_team_user",
        ),
    )

    team: Mapped["Team"] = relationship(
        "Team",
        back_populates="memberships",
    )
    user: Mapped["User"] = relationship(
        "User",
    )
