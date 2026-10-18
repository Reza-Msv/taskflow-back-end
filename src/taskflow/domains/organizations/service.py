import uuid

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.exceptions import DomainException
from taskflow.domains.identity.repository import UserRepository
from taskflow.domains.organizations.models import Organization, OrganizationMembership
from taskflow.domains.organizations.repository import OrganizationRepository
from taskflow.domains.organizations.schemas import (
    AddMembershipRequest,
    OrganizationCreateRequest,
    OrganizationUpdateRequest,
    UpdateMembershipRoleRequest,
)


class OrganizationService:
    """Service managing organizations and memberships with business constraints."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.org_repo = OrganizationRepository(session)
        self.user_repo = UserRepository(session)

    async def list_organizations(
        self,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        is_active: bool | None = None,
    ) -> tuple[list[Organization], int]:
        """List organizations with pagination, search, and active filter."""
        skip = (page - 1) * page_size
        orgs = await self.org_repo.list_organizations(
            skip=skip,
            limit=page_size,
            search=search,
            is_active=is_active,
        )
        total = await self.org_repo.count_organizations(
            search=search,
            is_active=is_active,
        )
        return orgs, total

    async def get_organization_by_id(self, org_id: uuid.UUID) -> Organization:
        """Fetch organization by ID or raise ORGANIZATION_NOT_FOUND."""
        org = await self.org_repo.get_by_id(org_id)
        if org is None:
            raise DomainException(
                code="ORGANIZATION_NOT_FOUND",
                message="Organization not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"organization_id": str(org_id)},
            )
        return org

    async def create_organization(
        self,
        request: OrganizationCreateRequest,
        creator_id: uuid.UUID | None = None,
    ) -> Organization:
        """Create a new organization, ensuring slug uniqueness."""
        existing = await self.org_repo.get_by_slug(request.slug)
        if existing is not None:
            raise DomainException(
                code="ORGANIZATION_SLUG_EXISTS",
                message=f"Organization with slug '{request.slug}' already exists.",
                status_code=status.HTTP_409_CONFLICT,
                details={"slug": request.slug},
            )

        org = await self.org_repo.create_organization(
            name=request.name,
            slug=request.slug,
        )

        # If a creator is provided, assign them as initial owner
        if creator_id is not None:
            await self.org_repo.add_membership(
                org_id=org.id,
                user_id=creator_id,
                role="owner",
            )

        await self.session.commit()
        await self.session.refresh(org)
        return org

    async def update_organization(
        self,
        org_id: uuid.UUID,
        request: OrganizationUpdateRequest,
    ) -> Organization:
        """Update organization details."""
        org = await self.get_organization_by_id(org_id)
        updated_org = await self.org_repo.update_organization(
            org=org,
            name=request.name,
            is_active=request.is_active,
        )
        await self.session.commit()
        await self.session.refresh(updated_org)
        return updated_org

    # ---------------- Membership management ---------------- #

    async def list_memberships(self, org_id: uuid.UUID) -> list[OrganizationMembership]:
        """List all members of an organization."""
        await self.get_organization_by_id(org_id)
        return await self.org_repo.list_memberships(org_id)

    async def add_member(
        self,
        org_id: uuid.UUID,
        request: AddMembershipRequest,
    ) -> OrganizationMembership:
        """Add an active member to an organization."""
        await self.get_organization_by_id(org_id)

        user = await self.user_repo.get_by_id(request.user_id)
        if user is None:
            raise DomainException(
                code="USER_NOT_FOUND",
                message="User to be added as member was not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"user_id": str(request.user_id)},
            )

        existing_membership = await self.org_repo.get_membership(
            org_id, request.user_id
        )
        if existing_membership is not None:
            raise DomainException(
                code="MEMBERSHIP_ALREADY_EXISTS",
                message="User is already a member of this organization.",
                status_code=status.HTTP_409_CONFLICT,
                details={
                    "organization_id": str(org_id),
                    "user_id": str(request.user_id),
                },
            )

        membership = await self.org_repo.add_membership(
            org_id=org_id,
            user_id=request.user_id,
            role=request.role,
        )
        await self.session.commit()
        # Fetch with user loaded
        fresh_membership = await self.org_repo.get_membership(org_id, request.user_id)
        return fresh_membership or membership

    async def update_membership(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        request: UpdateMembershipRoleRequest,
    ) -> OrganizationMembership:
        """
        Update member role or status, enforcing protection against
        demoting the last active owner.
        """
        await self.get_organization_by_id(org_id)

        membership = await self.org_repo.get_membership(org_id, user_id)
        if membership is None:
            raise DomainException(
                code="MEMBERSHIP_NOT_FOUND",
                message="Membership not found for the given organization and user.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"organization_id": str(org_id), "user_id": str(user_id)},
            )

        # PRD rule: Protection against deleting/demoting the last active owner
        if membership.role == "owner" and (
            (request.role is not None and request.role != "owner")
            or request.is_active is False
        ):
            owner_count = await self.org_repo.count_active_owners(org_id)
            if owner_count <= 1:
                raise DomainException(
                    code="LAST_OWNER_PROTECTION",
                    message=(
                        "Cannot remove or demote the last active owner of "
                        "the organization."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"organization_id": str(org_id)},
                )

        updated_membership = await self.org_repo.update_membership(
            membership=membership,
            role=request.role,
            is_active=request.is_active,
        )
        await self.session.commit()
        fresh = await self.org_repo.get_membership(org_id, user_id)
        return fresh or updated_membership

    async def remove_member(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Remove a member from an organization, enforcing last owner protection."""
        await self.get_organization_by_id(org_id)

        membership = await self.org_repo.get_membership(org_id, user_id)
        if membership is None:
            raise DomainException(
                code="MEMBERSHIP_NOT_FOUND",
                message="Membership not found for the given organization and user.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"organization_id": str(org_id), "user_id": str(user_id)},
            )

        if membership.role == "owner":
            owner_count = await self.org_repo.count_active_owners(org_id)
            if owner_count <= 1:
                raise DomainException(
                    code="LAST_OWNER_PROTECTION",
                    message="Cannot remove the last active owner of the organization.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"organization_id": str(org_id)},
                )

        await self.org_repo.delete_membership(org_id, user_id)
        await self.session.commit()
