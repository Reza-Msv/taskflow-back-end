import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.identity.models import User
from taskflow.domains.organizations.models import (
    Invitation,
    Organization,
    OrganizationMembership,
    Team,
    TeamMembership,
)
from taskflow.domains.projects.models import Project


class OrganizationRepository:
    """Persistence operations for Organization and OrganizationMembership entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, org_id: uuid.UUID) -> Organization | None:
        """Fetch organization by primary key."""
        stmt = select(Organization).where(Organization.id == org_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Organization | None:
        """Fetch organization by unique slug (case-insensitive)."""
        stmt = select(Organization).where(Organization.slug == slug.strip().lower())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_organization(self, name: str, slug: str) -> Organization:
        """Create and persist a new organization."""
        org = Organization(
            name=name.strip(),
            slug=slug.strip().lower(),
            is_active=True,
        )
        self.session.add(org)
        await self.session.flush()
        return org

    async def update_organization(
        self,
        org: Organization,
        name: str | None = None,
        is_active: bool | None = None,
    ) -> Organization:
        """Update organization details."""
        if name is not None:
            org.name = name.strip()
        if is_active is not None:
            org.is_active = is_active
        await self.session.flush()
        return org

    async def list_organizations(
        self,
        skip: int = 0,
        limit: int = 25,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> list[Organization]:
        """List organizations with pagination, search, and active filter."""
        stmt = select(Organization).order_by(Organization.created_at.desc())

        if search:
            search_pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                (Organization.name.ilike(search_pattern))
                | (Organization.slug.ilike(search_pattern))
            )
        if is_active is not None:
            stmt = stmt.where(Organization.is_active == is_active)

        stmt = stmt.offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def count_organizations(
        self,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> int:
        """Count total matching organizations."""
        stmt = select(func.count(Organization.id))
        if search:
            search_pattern = f"%{search.strip()}%"
            stmt = stmt.where(
                (Organization.name.ilike(search_pattern))
                | (Organization.slug.ilike(search_pattern))
            )
        if is_active is not None:
            stmt = stmt.where(Organization.is_active == is_active)

        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def list_user_organizations(
        self,
        user_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
    ) -> tuple[list[Organization], int]:
        """List organizations that the user belongs to with pagination."""
        base_query = (
            select(Organization)
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.is_active == True,  # noqa: E712
            )
        )
        count_query = (
            select(func.count(Organization.id))
            .join(
                OrganizationMembership,
                OrganizationMembership.organization_id == Organization.id,
            )
            .where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.is_active == True,  # noqa: E712
            )
        )

        total_res = await self.session.execute(count_query)
        total = int(total_res.scalar_one() or 0)

        stmt = (
            base_query.order_by(Organization.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all()), total

    # ---------------- Membership operations ---------------- #

    async def get_membership(
        self, org_id: uuid.UUID, user_id: uuid.UUID
    ) -> OrganizationMembership | None:
        """Fetch organization membership for a given user."""
        stmt = (
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.user))
            .where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.user_id == user_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_membership_by_id(
        self, membership_id: uuid.UUID
    ) -> OrganizationMembership | None:
        """Fetch organization membership by primary key."""
        stmt = (
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.user))
            .where(OrganizationMembership.id == membership_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_memberships(
        self,
        org_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
        search: str | None = None,
        role: str | None = None,
    ) -> tuple[list[OrganizationMembership], int]:
        """
        List memberships in an organization with user loaded, search,
        role filtering, and pagination.
        """
        count_stmt = select(func.count(OrganizationMembership.id)).where(
            OrganizationMembership.organization_id == org_id
        )
        stmt = (
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.user))
            .where(OrganizationMembership.organization_id == org_id)
        )

        if role:
            stmt = stmt.where(OrganizationMembership.role == role)
            count_stmt = count_stmt.where(OrganizationMembership.role == role)

        if search:
            search_pat = f"%{search.strip()}%"
            # Join user to search by full_name or email
            stmt = stmt.join(User, OrganizationMembership.user_id == User.id).where(
                or_(User.full_name.ilike(search_pat), User.email.ilike(search_pat))
            )
            count_stmt = count_stmt.join(
                User, OrganizationMembership.user_id == User.id
            ).where(or_(User.full_name.ilike(search_pat), User.email.ilike(search_pat)))

        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = (
            stmt.order_by(OrganizationMembership.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def add_membership(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str = "member",
    ) -> OrganizationMembership:
        """Add a member to an organization."""
        membership = OrganizationMembership(
            organization_id=org_id,
            user_id=user_id,
            role=role,
            is_active=True,
        )
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def update_membership(
        self,
        membership: OrganizationMembership,
        role: str | None = None,
        is_active: bool | None = None,
    ) -> OrganizationMembership:
        """Update role or active status of a membership."""
        if role is not None:
            membership.role = role
        if is_active is not None:
            membership.is_active = is_active
        await self.session.flush()
        return membership

    async def delete_membership(self, org_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        """Remove a member from an organization."""
        stmt = delete(OrganizationMembership).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount > 0)

    async def count_active_owners(self, org_id: uuid.UUID) -> int:
        """
        Count active owners in an organization to protect against
        deleting the last active owner.
        """
        stmt = select(func.count(OrganizationMembership.id)).where(
            OrganizationMembership.organization_id == org_id,
            OrganizationMembership.role == "owner",
            OrganizationMembership.is_active == True,  # noqa: E712
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    # ---------------- Invitation operations ---------------- #

    async def create_invitation(
        self,
        org_id: uuid.UUID,
        email: str,
        role: str,
        token: str,
        invited_by_id: uuid.UUID | None,
        expires_at: datetime,
    ) -> Invitation:
        """Create and persist a new invitation."""
        invitation = Invitation(
            organization_id=org_id,
            email=email.strip().lower(),
            role=role,
            token=token,
            invited_by_id=invited_by_id,
            expires_at=expires_at,
        )
        self.session.add(invitation)
        await self.session.flush()
        return invitation

    async def get_invitation_by_token(self, token: str) -> Invitation | None:
        """Fetch invitation by unique token with relations loaded."""
        stmt = (
            select(Invitation)
            .options(
                selectinload(Invitation.organization),
                selectinload(Invitation.invited_by),
            )
            .where(Invitation.token == token)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_invitation_by_id(self, invitation_id: uuid.UUID) -> Invitation | None:
        """Fetch invitation by ID with relations loaded."""
        stmt = (
            select(Invitation)
            .options(
                selectinload(Invitation.organization),
                selectinload(Invitation.invited_by),
            )
            .where(Invitation.id == invitation_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_invitation_by_email(
        self, org_id: uuid.UUID, email: str
    ) -> Invitation | None:
        """Fetch pending unrevoked invitation for an email in an organization."""
        stmt = select(Invitation).where(
            Invitation.organization_id == org_id,
            Invitation.email == email.strip().lower(),
            Invitation.accepted_at.is_(None),
            Invitation.revoked_at.is_(None),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def revoke_invitation(self, invitation: Invitation) -> Invitation:
        """Mark an invitation as revoked."""
        invitation.revoked_at = datetime.now(UTC)
        await self.session.flush()
        return invitation

    async def resend_invitation(
        self,
        invitation: Invitation,
        new_token: str,
        new_expires_at: datetime,
    ) -> Invitation:
        """Refresh invitation with new token, reset expiration and clear revocation."""
        invitation.token = new_token
        invitation.expires_at = new_expires_at
        invitation.revoked_at = None
        invitation.accepted_at = None
        await self.session.flush()
        return invitation

    async def list_invitations(
        self,
        org_id: uuid.UUID,
        pending_only: bool = True,
        status_filter: str | None = None,
    ) -> list[Invitation]:
        """List invitations for an organization with optional status filtering."""
        stmt = select(Invitation).where(Invitation.organization_id == org_id)

        now = datetime.now(UTC)
        if status_filter == "pending" or (pending_only and not status_filter):
            stmt = stmt.where(
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at > now,
            )
        elif status_filter == "accepted":
            stmt = stmt.where(Invitation.accepted_at.is_not(None))
        elif status_filter == "revoked":
            stmt = stmt.where(Invitation.revoked_at.is_not(None))
        elif status_filter == "expired":
            stmt = stmt.where(
                Invitation.accepted_at.is_(None),
                Invitation.revoked_at.is_(None),
                Invitation.expires_at <= now,
            )

        stmt = stmt.order_by(Invitation.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # ---------------- Team operations ---------------- #

    async def create_team(
        self,
        org_id: uuid.UUID,
        name: str,
        description: str | None = None,
    ) -> Team:
        """Create and persist a new team in the organization."""
        team = Team(
            organization_id=org_id,
            name=name.strip(),
            description=description.strip() if description else None,
        )
        self.session.add(team)
        await self.session.flush()
        return team

    async def get_team_by_id(self, team_id: uuid.UUID) -> Team | None:
        """Fetch team by primary key."""
        stmt = select(Team).where(Team.id == team_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_team_in_org(
        self, org_id: uuid.UUID, team_id: uuid.UUID
    ) -> Team | None:
        """Fetch team scoped by organization ID."""
        stmt = select(Team).where(
            Team.organization_id == org_id,
            Team.id == team_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_team_by_name(self, org_id: uuid.UUID, name: str) -> Team | None:
        """Fetch team in organization by exact name (case-insensitive)."""
        stmt = select(Team).where(
            Team.organization_id == org_id,
            func.lower(Team.name) == name.strip().lower(),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_teams(
        self,
        org_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
        search: str | None = None,
    ) -> tuple[list[tuple[Team, int]], int]:
        """List teams with aggregated member counts, search, and pagination."""
        member_count_subq = (
            select(func.count(TeamMembership.id))
            .where(TeamMembership.team_id == Team.id)
            .scalar_subquery()
            .label("member_count")
        )
        stmt = select(Team, member_count_subq).where(Team.organization_id == org_id)
        count_stmt = select(func.count(Team.id)).where(Team.organization_id == org_id)

        if search:
            search_pat = f"%{search.strip()}%"
            stmt = stmt.where(
                or_(Team.name.ilike(search_pat), Team.description.ilike(search_pat))
            )
            count_stmt = count_stmt.where(
                or_(Team.name.ilike(search_pat), Team.description.ilike(search_pat))
            )

        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = stmt.order_by(Team.created_at.asc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        teams_with_counts = [(row[0], int(row[1] or 0)) for row in result.all()]
        return teams_with_counts, total

    async def get_team_member_count(self, team_id: uuid.UUID) -> int:
        """Get total member count for a specific team."""
        stmt = select(func.count(TeamMembership.id)).where(
            TeamMembership.team_id == team_id
        )
        result = await self.session.execute(stmt)
        return int(result.scalar_one() or 0)

    async def update_team(
        self,
        team: Team,
        name: str | None = None,
        description: str | None = None,
    ) -> Team:
        """Update team attributes."""
        if name is not None:
            team.name = name.strip()
        if description is not None:
            team.description = description.strip() if description else None
        await self.session.flush()
        return team

    async def delete_team(self, team: Team) -> None:
        """Delete team entity."""
        await self.session.delete(team)
        await self.session.flush()

    # ---------------- Team Membership operations ---------------- #

    async def get_team_membership(
        self, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> TeamMembership | None:
        """Fetch team membership with user loaded."""
        stmt = (
            select(TeamMembership)
            .options(selectinload(TeamMembership.user))
            .where(
                TeamMembership.team_id == team_id,
                TeamMembership.user_id == user_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_team_memberships(self, team_id: uuid.UUID) -> list[TeamMembership]:
        """List all team memberships with user loaded."""
        stmt = (
            select(TeamMembership)
            .options(selectinload(TeamMembership.user))
            .where(TeamMembership.team_id == team_id)
            .order_by(TeamMembership.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def add_team_membership(
        self,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str = "member",
    ) -> TeamMembership:
        """Add user to team."""
        membership = TeamMembership(
            team_id=team_id,
            user_id=user_id,
            role=role,
        )
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def update_team_membership(
        self,
        membership: TeamMembership,
        role: str,
    ) -> TeamMembership:
        """Update role within team."""
        membership.role = role
        await self.session.flush()
        return membership

    async def delete_team_membership(
        self, team_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        """Remove user from team."""
        stmt = delete(TeamMembership).where(
            TeamMembership.team_id == team_id,
            TeamMembership.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount > 0)

    # ---------------- Workspace Stats & Ownership ---------------- #

    async def get_workspace_stats(self, org_id: uuid.UUID) -> dict[str, int]:
        """Aggregate workspace metrics in a single database roundtrip."""
        stmt = select(
            select(func.count(OrganizationMembership.id))
            .where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.is_active == True,  # noqa: E712
            )
            .scalar_subquery()
            .label("member_count"),
            select(func.count(OrganizationMembership.id))
            .where(
                OrganizationMembership.organization_id == org_id,
                OrganizationMembership.role == "owner",
                OrganizationMembership.is_active == True,  # noqa: E712
            )
            .scalar_subquery()
            .label("active_owners_count"),
            select(func.count(Team.id))
            .where(Team.organization_id == org_id)
            .scalar_subquery()
            .label("team_count"),
            select(func.count(Project.id))
            .where(Project.organization_id == org_id)
            .scalar_subquery()
            .label("project_count"),
        )
        res = await self.session.execute(stmt)
        row = res.one()

        return {
            "member_count": int(row.member_count or 0),
            "active_owners_count": int(row.active_owners_count or 0),
            "team_count": int(row.team_count or 0),
            "project_count": int(row.project_count or 0),
        }

    async def transfer_ownership(
        self,
        org_id: uuid.UUID,
        current_owner_user_id: uuid.UUID,
        new_owner_user_id: uuid.UUID,
    ) -> None:
        """
        Promote new_owner to 'owner' and demote current_owner to 'admin'.
        Both operations occur atomically in the ongoing transaction.
        """
        new_owner_m = await self.get_membership(org_id, new_owner_user_id)
        if new_owner_m:
            new_owner_m.role = "owner"

        old_owner_m = await self.get_membership(org_id, current_owner_user_id)
        if old_owner_m:
            old_owner_m.role = "admin"

        await self.session.flush()
