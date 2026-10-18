import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.organizations.models import Organization, OrganizationMembership


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

    async def list_memberships(self, org_id: uuid.UUID) -> list[OrganizationMembership]:
        """List all memberships in an organization with user loaded."""
        stmt = (
            select(OrganizationMembership)
            .options(selectinload(OrganizationMembership.user))
            .where(OrganizationMembership.organization_id == org_id)
            .order_by(OrganizationMembership.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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
