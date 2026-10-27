import csv
import io
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.exceptions import DomainException
from taskflow.domains.identity.repository import UserRepository
from taskflow.domains.organizations.models import (
    Invitation,
    Organization,
    OrganizationMembership,
    Team,
    TeamMembership,
)
from taskflow.domains.organizations.repository import OrganizationRepository
from taskflow.domains.organizations.schemas import (
    AddMembershipRequest,
    AddTeamMemberRequest,
    CreateInvitationRequest,
    InvitationDetailResponse,
    OrganizationCreateRequest,
    OrganizationUpdateRequest,
    TeamCreateRequest,
    TeamUpdateRequest,
    TransferOwnershipRequest,
    UpdateMembershipRoleRequest,
    UpdateTeamMemberRoleRequest,
    WorkspaceStatsResponse,
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

    async def list_user_organizations(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Organization], int]:
        """List organizations that the user belongs to with pagination."""
        skip = (page - 1) * page_size
        return await self.org_repo.list_user_organizations(
            user_id=user_id,
            skip=skip,
            limit=page_size,
        )

    # ---------------- Membership management ---------------- #

    async def list_memberships(
        self,
        org_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
        role: str | None = None,
    ) -> tuple[list[OrganizationMembership], int]:
        """List members of an organization with search, role filter, and pagination."""
        await self.get_organization_by_id(org_id)
        skip = (page - 1) * page_size
        return await self.org_repo.list_memberships(
            org_id=org_id,
            skip=skip,
            limit=page_size,
            search=search,
            role=role,
        )

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

    async def delete_organization(self, org_id: uuid.UUID) -> None:
        """Delete an organization and its associated resources."""
        org = await self.get_organization_by_id(org_id)
        await self.session.delete(org)
        await self.session.commit()

    # ---------------- Invitation management ---------------- #

    async def create_invitation(
        self,
        org_id: uuid.UUID,
        request: CreateInvitationRequest,
        invited_by_id: uuid.UUID,
        expires_days: int = 7,
    ) -> Invitation:
        """Create a new invitation to join the organization."""
        await self.get_organization_by_id(org_id)

        # Check if user already exists and is already a member
        existing_user = await self.user_repo.get_by_email(request.email)
        if existing_user is not None:
            existing_membership = await self.org_repo.get_membership(
                org_id, existing_user.id
            )
            if existing_membership is not None and existing_membership.is_active:
                raise DomainException(
                    code="MEMBERSHIP_ALREADY_EXISTS",
                    message="User is already an active member of this organization.",
                    status_code=status.HTTP_409_CONFLICT,
                    details={"organization_id": str(org_id), "email": request.email},
                )

        # Check if there is already an active/pending invitation for this email
        pending_invitation = await self.org_repo.get_active_invitation_by_email(
            org_id, request.email
        )
        if pending_invitation is not None:
            # Check expiration
            expires_at = pending_invitation.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at > datetime.now(UTC):
                raise DomainException(
                    code="INVITATION_ALREADY_EXISTS",
                    message=(
                        "An active invitation for this email address already exists."
                    ),
                    status_code=status.HTTP_409_CONFLICT,
                    details={"organization_id": str(org_id), "email": request.email},
                )

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(days=expires_days)

        invitation = await self.org_repo.create_invitation(
            org_id=org_id,
            email=request.email,
            role=request.role,
            token=token,
            invited_by_id=invited_by_id,
            expires_at=expires_at,
        )
        await self.session.commit()
        await self.session.refresh(invitation)
        return invitation

    async def list_invitations(
        self,
        org_id: uuid.UUID,
        pending_only: bool = True,
        status_filter: str | None = None,
    ) -> list[Invitation]:
        """List invitations for an organization with optional status filtering."""
        await self.get_organization_by_id(org_id)
        return await self.org_repo.list_invitations(
            org_id,
            pending_only=pending_only,
            status_filter=status_filter,
        )

    async def revoke_invitation(
        self, org_id: uuid.UUID, invitation_id: uuid.UUID
    ) -> Invitation:
        """Revoke a pending invitation."""
        await self.get_organization_by_id(org_id)
        invitation = await self.org_repo.get_invitation_by_id(invitation_id)
        if invitation is None or invitation.organization_id != org_id:
            raise DomainException(
                code="INVITATION_NOT_FOUND",
                message="Invitation not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"invitation_id": str(invitation_id)},
            )

        if invitation.accepted_at is not None:
            raise DomainException(
                code="INVITATION_ALREADY_ACCEPTED",
                message="Cannot revoke an already accepted invitation.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if invitation.revoked_at is not None:
            raise DomainException(
                code="INVITATION_ALREADY_REVOKED",
                message="This invitation has already been revoked.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        revoked = await self.org_repo.revoke_invitation(invitation)
        await self.session.commit()
        await self.session.refresh(revoked)
        return revoked

    async def resend_invitation(
        self,
        org_id: uuid.UUID,
        invitation_id: uuid.UUID,
        expires_days: int = 7,
    ) -> Invitation:
        """Resend invitation by generating a new token and resetting expiration."""
        await self.get_organization_by_id(org_id)
        invitation = await self.org_repo.get_invitation_by_id(invitation_id)
        if invitation is None or invitation.organization_id != org_id:
            raise DomainException(
                code="INVITATION_NOT_FOUND",
                message="Invitation not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"invitation_id": str(invitation_id)},
            )

        if invitation.accepted_at is not None:
            raise DomainException(
                code="INVITATION_ALREADY_ACCEPTED",
                message="Cannot resend an already accepted invitation.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        new_token = secrets.token_urlsafe(32)
        new_expires_at = datetime.now(UTC) + timedelta(days=expires_days)

        resent = await self.org_repo.resend_invitation(
            invitation=invitation,
            new_token=new_token,
            new_expires_at=new_expires_at,
        )
        await self.session.commit()
        await self.session.refresh(resent)
        return resent

    async def get_invitation_public_details(
        self, token: str
    ) -> InvitationDetailResponse:
        """
        Public endpoint verifying token validity without leaking
        internal account details or member directories.
        """
        invitation = await self.org_repo.get_invitation_by_token(token)
        if invitation is None:
            raise DomainException(
                code="INVITATION_NOT_FOUND",
                message="Invitation not found or invalid token.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        st = invitation.status
        is_valid = st == "pending"
        org_name = invitation.organization.name if invitation.organization else ""
        inviter_name = (
            invitation.invited_by.full_name if invitation.invited_by else None
        )

        return InvitationDetailResponse(
            id=invitation.id,
            organization_id=invitation.organization_id,
            organization_name=org_name,
            email=invitation.email,
            role=invitation.role,
            invited_by_name=inviter_name,
            status=st,
            is_valid=is_valid,
            expires_at=invitation.expires_at,
        )

    async def accept_invitation(
        self, token: str, current_user_id: uuid.UUID
    ) -> OrganizationMembership:
        """Accept an invitation token and add current user to the organization."""
        invitation = await self.org_repo.get_invitation_by_token(token)
        if invitation is None:
            raise DomainException(
                code="INVITATION_NOT_FOUND",
                message="Invitation not found or invalid token.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if invitation.accepted_at is not None:
            raise DomainException(
                code="INVITATION_ALREADY_ACCEPTED",
                message="This invitation has already been accepted.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if invitation.revoked_at is not None:
            raise DomainException(
                code="INVITATION_REVOKED",
                message="This invitation has been revoked.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        expires_at = invitation.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < datetime.now(UTC):
            raise DomainException(
                code="INVITATION_EXPIRED",
                message="This invitation has expired.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        user = await self.user_repo.get_by_id(current_user_id)
        if user is None:
            raise DomainException(
                code="USER_NOT_FOUND",
                message="Authenticated user not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Check existing membership
        existing_membership = await self.org_repo.get_membership(
            invitation.organization_id, current_user_id
        )
        if existing_membership is not None:
            invitation.accepted_at = datetime.now(UTC)
            await self.session.commit()
            return existing_membership

        membership = await self.org_repo.add_membership(
            org_id=invitation.organization_id,
            user_id=current_user_id,
            role=invitation.role,
        )
        invitation.accepted_at = datetime.now(UTC)
        await self.session.commit()
        fresh = await self.org_repo.get_membership(
            invitation.organization_id, current_user_id
        )
        return fresh or membership

    # ---------------- Workspace Stats & Ownership ---------------- #

    async def get_workspace_stats(self, org_id: uuid.UUID) -> WorkspaceStatsResponse:
        """Retrieve aggregated metrics for the organization workspace."""
        await self.get_organization_by_id(org_id)
        stats = await self.org_repo.get_workspace_stats(org_id)
        return WorkspaceStatsResponse(
            organization_id=org_id,
            member_count=stats["member_count"],
            team_count=stats["team_count"],
            project_count=stats["project_count"],
            active_owners_count=stats["active_owners_count"],
        )

    async def transfer_ownership(
        self,
        org_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        request: TransferOwnershipRequest,
    ) -> OrganizationMembership:
        """
        Transfer primary organization ownership to another existing active member.
        The current owner is safely demoted to 'admin'.
        """
        await self.get_organization_by_id(org_id)

        actor_membership = await self.org_repo.get_membership(org_id, actor_user_id)
        if (
            actor_membership is None
            or not actor_membership.is_active
            or actor_membership.role != "owner"
        ):
            raise DomainException(
                code="FORBIDDEN",
                message="Only an active organization owner can transfer ownership.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        if actor_user_id == request.new_owner_id:
            raise DomainException(
                code="INVALID_OWNERSHIP_TRANSFER",
                message="Cannot transfer ownership to yourself.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        target_membership = await self.org_repo.get_membership(
            org_id, request.new_owner_id
        )
        if target_membership is None or not target_membership.is_active:
            raise DomainException(
                code="TARGET_MEMBER_NOT_FOUND",
                message=(
                    "Target user must be an active member of the organization "
                    "to receive ownership."
                ),
                status_code=status.HTTP_404_NOT_FOUND,
                details={"target_user_id": str(request.new_owner_id)},
            )

        await self.org_repo.transfer_ownership(
            org_id=org_id,
            current_owner_user_id=actor_user_id,
            new_owner_user_id=request.new_owner_id,
        )
        await self.session.commit()

        fresh_target = await self.org_repo.get_membership(org_id, request.new_owner_id)
        return fresh_target or target_membership

    async def export_members_csv(self, org_id: uuid.UUID) -> str:
        """Generate CSV string of organization members."""
        await self.get_organization_by_id(org_id)
        members, _ = await self.org_repo.list_memberships(org_id, skip=0, limit=10000)

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Member ID",
                "User ID",
                "Full Name",
                "Email",
                "Role",
                "Active",
                "Joined At",
            ]
        )

        for m in members:
            full_name = m.user.full_name if m.user else ""
            email = m.user.email if m.user else ""
            created_str = m.created_at.isoformat() if m.created_at else ""
            writer.writerow(
                [
                    str(m.id),
                    str(m.user_id),
                    full_name,
                    email,
                    m.role,
                    str(m.is_active),
                    created_str,
                ]
            )

        return output.getvalue()

    # ---------------- Team Management ---------------- #

    async def list_teams(
        self,
        org_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
        search: str | None = None,
    ) -> tuple[list[tuple[Team, int]], int]:
        """List organization teams with member count, search, and pagination."""
        await self.get_organization_by_id(org_id)
        skip = (page - 1) * page_size
        return await self.org_repo.list_teams(
            org_id=org_id,
            skip=skip,
            limit=page_size,
            search=search,
        )

    async def get_team(
        self, org_id: uuid.UUID, team_id: uuid.UUID
    ) -> tuple[Team, int, list[TeamMembership]]:
        """Fetch team with member count and membership details."""
        await self.get_organization_by_id(org_id)
        team = await self.org_repo.get_team_in_org(org_id, team_id)
        if team is None:
            raise DomainException(
                code="TEAM_NOT_FOUND",
                message="Team not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id)},
            )
        count = await self.org_repo.get_team_member_count(team_id)
        members = await self.org_repo.list_team_memberships(team_id)
        return team, count, members

    async def create_team(
        self, org_id: uuid.UUID, request: TeamCreateRequest
    ) -> tuple[Team, int]:
        """Create a new team within an organization, checking name uniqueness."""
        await self.get_organization_by_id(org_id)

        existing = await self.org_repo.get_team_by_name(org_id, request.name)
        if existing is not None:
            raise DomainException(
                code="TEAM_NAME_EXISTS",
                message=f"Team '{request.name.strip()}' already exists.",
                status_code=status.HTTP_409_CONFLICT,
                details={"name": request.name},
            )

        team = await self.org_repo.create_team(
            org_id=org_id,
            name=request.name,
            description=request.description,
        )
        await self.session.commit()
        await self.session.refresh(team)
        return team, 0

    async def update_team(
        self,
        org_id: uuid.UUID,
        team_id: uuid.UUID,
        request: TeamUpdateRequest,
    ) -> tuple[Team, int]:
        """Update team details."""
        await self.get_organization_by_id(org_id)
        team = await self.org_repo.get_team_in_org(org_id, team_id)
        if team is None:
            raise DomainException(
                code="TEAM_NOT_FOUND",
                message="Team not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id)},
            )

        if (
            request.name is not None
            and request.name.strip().lower() != team.name.lower()
        ):
            existing = await self.org_repo.get_team_by_name(org_id, request.name)
            if existing is not None and existing.id != team_id:
                raise DomainException(
                    code="TEAM_NAME_EXISTS",
                    message=(
                        f"Team '{request.name.strip()}' already exists in organization."
                    ),
                    status_code=status.HTTP_409_CONFLICT,
                    details={"name": request.name},
                )

        updated_team = await self.org_repo.update_team(
            team=team,
            name=request.name,
            description=request.description,
        )
        await self.session.commit()
        await self.session.refresh(updated_team)
        count = await self.org_repo.get_team_member_count(team_id)
        return updated_team, count

    async def delete_team(self, org_id: uuid.UUID, team_id: uuid.UUID) -> None:
        """Delete a team from the organization."""
        await self.get_organization_by_id(org_id)
        team = await self.org_repo.get_team_in_org(org_id, team_id)
        if team is None:
            raise DomainException(
                code="TEAM_NOT_FOUND",
                message="Team not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id)},
            )

        await self.org_repo.delete_team(team)
        await self.session.commit()

    async def list_team_members(
        self, org_id: uuid.UUID, team_id: uuid.UUID
    ) -> list[TeamMembership]:
        """List all members associated with a team."""
        await self.get_organization_by_id(org_id)
        team = await self.org_repo.get_team_in_org(org_id, team_id)
        if team is None:
            raise DomainException(
                code="TEAM_NOT_FOUND",
                message="Team not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id)},
            )
        return await self.org_repo.list_team_memberships(team_id)

    async def add_team_member(
        self,
        org_id: uuid.UUID,
        team_id: uuid.UUID,
        request: AddTeamMemberRequest,
    ) -> TeamMembership:
        """
        Add a user to a team. Enforces workspace boundary: user MUST be
        an active member of the organization before joining a team.
        """
        await self.get_organization_by_id(org_id)
        team = await self.org_repo.get_team_in_org(org_id, team_id)
        if team is None:
            raise DomainException(
                code="TEAM_NOT_FOUND",
                message="Team not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id)},
            )

        # Workspace boundary check:
        org_membership = await self.org_repo.get_membership(org_id, request.user_id)
        if org_membership is None or not org_membership.is_active:
            raise DomainException(
                code="USER_NOT_ORGANIZATION_MEMBER",
                message=(
                    "Cannot add user to team: user is not an active member "
                    "of this organization workspace."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "user_id": str(request.user_id),
                    "organization_id": str(org_id),
                },
            )

        existing_membership = await self.org_repo.get_team_membership(
            team_id, request.user_id
        )
        if existing_membership is not None:
            raise DomainException(
                code="TEAM_MEMBERSHIP_ALREADY_EXISTS",
                message="User is already a member of this team.",
                status_code=status.HTTP_409_CONFLICT,
                details={"team_id": str(team_id), "user_id": str(request.user_id)},
            )

        membership = await self.org_repo.add_team_membership(
            team_id=team_id,
            user_id=request.user_id,
            role=request.role,
        )
        await self.session.commit()
        fresh = await self.org_repo.get_team_membership(team_id, request.user_id)
        return fresh or membership

    async def update_team_member(
        self,
        org_id: uuid.UUID,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
        request: UpdateTeamMemberRoleRequest,
    ) -> TeamMembership:
        """Update role of a member in a team."""
        await self.get_organization_by_id(org_id)
        team = await self.org_repo.get_team_in_org(org_id, team_id)
        if team is None:
            raise DomainException(
                code="TEAM_NOT_FOUND",
                message="Team not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id)},
            )

        membership = await self.org_repo.get_team_membership(team_id, user_id)
        if membership is None:
            raise DomainException(
                code="TEAM_MEMBERSHIP_NOT_FOUND",
                message="User is not a member of this team.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id), "user_id": str(user_id)},
            )

        updated = await self.org_repo.update_team_membership(
            membership=membership,
            role=request.role,
        )
        await self.session.commit()
        fresh = await self.org_repo.get_team_membership(team_id, user_id)
        return fresh or updated

    async def remove_team_member(
        self,
        org_id: uuid.UUID,
        team_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Remove a member from a team."""
        await self.get_organization_by_id(org_id)
        team = await self.org_repo.get_team_in_org(org_id, team_id)
        if team is None:
            raise DomainException(
                code="TEAM_NOT_FOUND",
                message="Team not found in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id)},
            )

        membership = await self.org_repo.get_team_membership(team_id, user_id)
        if membership is None:
            raise DomainException(
                code="TEAM_MEMBERSHIP_NOT_FOUND",
                message="User is not a member of this team.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"team_id": str(team_id), "user_id": str(user_id)},
            )

        await self.org_repo.delete_team_membership(team_id, user_id)
        await self.session.commit()
