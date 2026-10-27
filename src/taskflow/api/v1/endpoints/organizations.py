import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from taskflow.api.deps import (
    get_current_active_user,
    get_organization_service,
)
from taskflow.core.exceptions import DomainException
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import MessageResponse, UserResponse
from taskflow.domains.organizations.models import OrganizationMembership, TeamMembership
from taskflow.domains.organizations.schemas import (
    AddMembershipRequest,
    AddTeamMemberRequest,
    CreateInvitationRequest,
    InvitationDetailResponse,
    InvitationListResponse,
    InvitationResponse,
    OrganizationCreateRequest,
    OrganizationListResponse,
    OrganizationMembershipListResponse,
    OrganizationMembershipResponse,
    OrganizationResponse,
    OrganizationUpdateRequest,
    TeamCreateRequest,
    TeamDetailResponse,
    TeamListResponse,
    TeamMemberListResponse,
    TeamMemberResponse,
    TeamResponse,
    TeamUpdateRequest,
    TransferOwnershipRequest,
    UpdateMembershipRoleRequest,
    UpdateTeamMemberRoleRequest,
    WorkspaceStatsResponse,
)
from taskflow.domains.organizations.service import OrganizationService

router = APIRouter(prefix="/organizations", tags=["Organizations & Memberships"])

ROLE_HIERARCHY = {
    "owner": 4,
    "admin": 3,
    "member": 2,
    "guest": 1,
    "viewer": 1,
}


async def get_org_membership_for_user(
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    org_service: OrganizationService,
) -> OrganizationMembership:
    """Helper verifying active membership in organization."""
    membership = await org_service.org_repo.get_membership(org_id, user_id)
    if membership is None or not membership.is_active:
        raise DomainException(
            code="FORBIDDEN",
            message="Access denied: You are not an active member of this organization.",
            status_code=status.HTTP_403_FORBIDDEN,
            details={"organization_id": str(org_id)},
        )
    return membership


def serialize_team_membership(m: TeamMembership) -> TeamMemberResponse:
    """Helper formatting team membership response with nested user."""
    user_dto = UserResponse.model_validate(m.user) if m.user else None
    return TeamMemberResponse(
        id=m.id,
        team_id=m.team_id,
        user_id=m.user_id,
        role=m.role,
        created_at=m.created_at,
        user=user_dto,
    )


# -------------------------------------------------------------
# Organization Core Endpoints
# -------------------------------------------------------------


@router.post(
    "",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Organization",
)
async def create_organization(
    request: OrganizationCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationResponse:
    """
    Create a new organization for an authenticated user and make the creator its owner.
    """
    org = await org_service.create_organization(
        request=request,
        creator_id=current_user.id,
    )
    return OrganizationResponse.model_validate(org)


@router.get(
    "",
    response_model=OrganizationListResponse,
    summary="List Organizations Current User Belongs To",
)
async def list_my_organizations(
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
) -> OrganizationListResponse:
    """List organizations the authenticated user belongs to."""
    orgs, total = await org_service.list_user_organizations(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
    return OrganizationListResponse(
        items=[OrganizationResponse.model_validate(o) for o in orgs],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/invitations/details",
    response_model=InvitationDetailResponse,
    summary="Verify & View Invitation Details (Public)",
)
async def get_invitation_details(
    token: Annotated[str, Query(..., description="Invitation token")],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> InvitationDetailResponse:
    """
    Public endpoint allowing invitees to verify invitation validity and inspect
    the organization and inviter names prior to signing up or authenticating.
    """
    return await org_service.get_invitation_public_details(token=token)


@router.get(
    "/{organization_id}",
    response_model=OrganizationResponse,
    summary="Get Organization Details",
)
async def get_organization(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationResponse:
    """Retrieve organization details. Requires active membership."""
    await get_org_membership_for_user(organization_id, current_user.id, org_service)
    org = await org_service.get_organization_by_id(organization_id)
    return OrganizationResponse.model_validate(org)


@router.patch(
    "/{organization_id}",
    response_model=OrganizationResponse,
    summary="Update Organization",
)
async def update_organization(
    organization_id: uuid.UUID,
    request: OrganizationUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationResponse:
    """Update organization details. Requires owner or admin role."""
    membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message=(
                "Only organization owners or admins can update organization settings."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )
    org = await org_service.update_organization(organization_id, request)
    return OrganizationResponse.model_validate(org)


@router.delete(
    "/{organization_id}",
    response_model=MessageResponse,
    summary="Delete Organization",
)
async def delete_organization(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """Delete organization. Requires owner role."""
    membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if membership.role != "owner":
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners can delete the organization.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    await org_service.delete_organization(organization_id)
    return MessageResponse(message="Organization deleted successfully.")


@router.get(
    "/{organization_id}/stats",
    response_model=WorkspaceStatsResponse,
    summary="Get Workspace Overview Statistics",
)
async def get_workspace_stats(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> WorkspaceStatsResponse:
    """Retrieve workspace statistics. Requires active membership."""
    await get_org_membership_for_user(organization_id, current_user.id, org_service)
    return await org_service.get_workspace_stats(organization_id)


@router.post(
    "/{organization_id}/transfer-ownership",
    response_model=OrganizationMembershipResponse,
    summary="Transfer Organization Primary Ownership",
)
async def transfer_organization_ownership(
    organization_id: uuid.UUID,
    request: TransferOwnershipRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationMembershipResponse:
    """
    Safely transfer primary organization ownership to another existing member.
    The caller is safely demoted to admin.
    """
    membership = await org_service.transfer_ownership(
        org_id=organization_id,
        actor_user_id=current_user.id,
        request=request,
    )
    user_dto = UserResponse.model_validate(membership.user) if membership.user else None
    return OrganizationMembershipResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        role=membership.role,
        is_active=membership.is_active,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
        user=user_dto,
    )


@router.get(
    "/{organization_id}/members/export",
    summary="Export Organization Members as CSV",
)
async def export_organization_members(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> Response:
    """Export member directory as CSV. Requires active membership."""
    await get_org_membership_for_user(organization_id, current_user.id, org_service)
    csv_content = await org_service.export_members_csv(organization_id)
    filename = f"org_{organization_id}_members.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -------------------------------------------------------------
# Organization Members Endpoints
# -------------------------------------------------------------


@router.get(
    "/{organization_id}/members",
    response_model=OrganizationMembershipListResponse,
    summary="List Organization Members",
)
async def list_organization_members(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search by member name or email"),
    role: str | None = Query(None, description="Filter by member role"),
) -> OrganizationMembershipListResponse:
    """List members of an organization with pagination, search, and role filter."""
    await get_org_membership_for_user(organization_id, current_user.id, org_service)
    memberships, total = await org_service.list_memberships(
        organization_id,
        page=page,
        page_size=page_size,
        search=search,
        role=role,
    )
    items = []
    for m in memberships:
        user_dto = UserResponse.model_validate(m.user) if m.user else None
        items.append(
            OrganizationMembershipResponse(
                id=m.id,
                organization_id=m.organization_id,
                user_id=m.user_id,
                role=m.role,
                is_active=m.is_active,
                created_at=m.created_at,
                updated_at=m.updated_at,
                user=user_dto,
            )
        )
    return OrganizationMembershipListResponse(
        items=items, page=page, page_size=page_size, total=total
    )


@router.post(
    "/{organization_id}/members",
    response_model=OrganizationMembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Member to Organization",
)
async def add_organization_member(
    organization_id: uuid.UUID,
    request: AddMembershipRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationMembershipResponse:
    """
    Directly add an existing user as member to the organization.
    Requires owner or admin role. Admin cannot assign owner role.
    """
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can add members.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Prevent role escalation: admin cannot add an owner
    if request.role == "owner" and actor_membership.role != "owner":
        raise DomainException(
            code="UNAUTHORIZED_ROLE_ESCALATION",
            message="Only organization owners can grant the owner role.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    membership = await org_service.add_member(organization_id, request)
    user_dto = UserResponse.model_validate(membership.user) if membership.user else None
    return OrganizationMembershipResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        role=membership.role,
        is_active=membership.is_active,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
        user=user_dto,
    )


@router.patch(
    "/{organization_id}/members/{user_id}",
    response_model=OrganizationMembershipResponse,
    summary="Update Member Role or Status",
)
async def update_organization_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    request: UpdateMembershipRoleRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationMembershipResponse:
    """
    Update a member's role or status.
    - Owner can change any member's role (except demoting the last owner).
    - Admin can change non-owner roles to non-owner roles.
      Admin cannot touch owners or promote to owner.
    """
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can update member roles.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    target_membership = await org_service.org_repo.get_membership(
        organization_id, user_id
    )
    if target_membership is None:
        raise DomainException(
            code="MEMBERSHIP_NOT_FOUND",
            message="Membership not found for the given organization and user.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Admin restrictions:
    if actor_membership.role == "admin":
        if target_membership.role == "owner":
            raise DomainException(
                code="FORBIDDEN",
                message="Admins cannot modify organization owners.",
                status_code=status.HTTP_403_FORBIDDEN,
            )
        if request.role == "owner":
            raise DomainException(
                code="UNAUTHORIZED_ROLE_ESCALATION",
                message="Admins cannot promote members to owner.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

    membership = await org_service.update_membership(organization_id, user_id, request)
    user_dto = UserResponse.model_validate(membership.user) if membership.user else None
    return OrganizationMembershipResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        role=membership.role,
        is_active=membership.is_active,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
        user=user_dto,
    )


@router.delete(
    "/{organization_id}/members/{user_id}",
    response_model=MessageResponse,
    summary="Remove Member from Organization",
)
async def remove_organization_member(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """
    Remove member from organization.
    - User can remove themselves (leave organization), unless they are the last owner.
    - Owner can remove any member (protected against deleting last owner).
    - Admin can remove members/guests/viewers, but cannot remove owners.
    """
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )

    is_self = current_user.id == user_id

    if not is_self and actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message=(
                "You do not have permission to remove members from this organization."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    target_membership = await org_service.org_repo.get_membership(
        organization_id, user_id
    )
    if target_membership is None:
        raise DomainException(
            code="MEMBERSHIP_NOT_FOUND",
            message="Membership not found for the given organization and user.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if (
        not is_self
        and actor_membership.role == "admin"
        and target_membership.role == "owner"
    ):
        raise DomainException(
            code="FORBIDDEN",
            message="Admins cannot remove organization owners.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await org_service.remove_member(organization_id, user_id)
    return MessageResponse(message="Member removed successfully from organization.")


# -------------------------------------------------------------
# Invitation Flow Endpoints
# -------------------------------------------------------------


@router.post(
    "/{organization_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Organization Invitation",
)
async def create_organization_invitation(
    organization_id: uuid.UUID,
    request: CreateInvitationRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> InvitationResponse:
    """
    Invite a user by email to join the organization.
    Requires owner or admin role. Admin cannot invite as owner.
    """
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can invite members.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    if request.role == "owner" and actor_membership.role != "owner":
        raise DomainException(
            code="UNAUTHORIZED_ROLE_ESCALATION",
            message="Admins cannot invite users with the owner role.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    invitation = await org_service.create_invitation(
        org_id=organization_id,
        request=request,
        invited_by_id=current_user.id,
    )
    return InvitationResponse.model_validate(invitation)


@router.get(
    "/{organization_id}/invitations",
    response_model=InvitationListResponse,
    summary="List Organization Invitations",
)
async def list_organization_invitations(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    status_filter: str | None = Query(
        None,
        alias="status",
        description="Filter by status: pending, accepted, expired, revoked, or all",
    ),
    pending_only: bool = Query(
        True, description="Default to pending only if status is not set"
    ),
) -> InvitationListResponse:
    """List invitations for an organization. Requires owner or admin role."""
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can view invitations.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    invitations = await org_service.list_invitations(
        organization_id,
        pending_only=pending_only,
        status_filter=status_filter,
    )
    items = [InvitationResponse.model_validate(inv) for inv in invitations]
    return InvitationListResponse(items=items, total=len(items))


@router.post(
    "/{organization_id}/invitations/{invitation_id}/revoke",
    response_model=InvitationResponse,
    summary="Revoke Organization Invitation",
)
@router.delete(
    "/{organization_id}/invitations/{invitation_id}",
    response_model=InvitationResponse,
    summary="Revoke/Cancel Organization Invitation",
)
async def revoke_organization_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> InvitationResponse:
    """Revoke a pending organization invitation. Requires owner or admin role."""
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can revoke invitations.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    revoked = await org_service.revoke_invitation(
        org_id=organization_id,
        invitation_id=invitation_id,
    )
    return InvitationResponse.model_validate(revoked)


@router.post(
    "/{organization_id}/invitations/{invitation_id}/resend",
    response_model=InvitationResponse,
    summary="Resend Organization Invitation",
)
async def resend_organization_invitation(
    organization_id: uuid.UUID,
    invitation_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> InvitationResponse:
    """
    Resend an invitation with a refreshed token and reset expiration.
    Requires owner or admin role.
    """
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can resend invitations.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    resent = await org_service.resend_invitation(
        org_id=organization_id,
        invitation_id=invitation_id,
    )
    return InvitationResponse.model_validate(resent)


@router.post(
    "/invitations/accept",
    response_model=OrganizationMembershipResponse,
    summary="Accept Organization Invitation",
)
async def accept_invitation(
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    token: str = Query(..., description="Invitation token"),
) -> OrganizationMembershipResponse:
    """Accept an organization invitation token for the current user."""
    membership = await org_service.accept_invitation(
        token=token, current_user_id=current_user.id
    )
    user_dto = UserResponse.model_validate(membership.user) if membership.user else None
    return OrganizationMembershipResponse(
        id=membership.id,
        organization_id=membership.organization_id,
        user_id=membership.user_id,
        role=membership.role,
        is_active=membership.is_active,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
        user=user_dto,
    )


# -------------------------------------------------------------
# Teams Endpoints
# -------------------------------------------------------------


@router.post(
    "/{organization_id}/teams",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Team in Organization",
)
async def create_team(
    organization_id: uuid.UUID,
    request: TeamCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TeamResponse:
    """
    Create a new functional team within an organization.
    Requires owner or admin role.
    """
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can create teams.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    team, member_count = await org_service.create_team(
        org_id=organization_id,
        request=request,
    )
    return TeamResponse(
        id=team.id,
        organization_id=team.organization_id,
        name=team.name,
        description=team.description,
        member_count=member_count,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.get(
    "/{organization_id}/teams",
    response_model=TeamListResponse,
    summary="List Teams in Organization",
)
async def list_teams(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search by team name or description"),
) -> TeamListResponse:
    """List teams in organization with member counts. Requires active membership."""
    await get_org_membership_for_user(organization_id, current_user.id, org_service)
    teams_with_counts, total = await org_service.list_teams(
        org_id=organization_id,
        page=page,
        page_size=page_size,
        search=search,
    )
    items = [
        TeamResponse(
            id=team.id,
            organization_id=team.organization_id,
            name=team.name,
            description=team.description,
            member_count=count,
            created_at=team.created_at,
            updated_at=team.updated_at,
        )
        for team, count in teams_with_counts
    ]
    return TeamListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{organization_id}/teams/{team_id}",
    response_model=TeamDetailResponse,
    summary="Get Team Details",
)
async def get_team(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TeamDetailResponse:
    """
    Get detailed team view including all member records.
    Requires active membership.
    """
    await get_org_membership_for_user(organization_id, current_user.id, org_service)
    team, count, members = await org_service.get_team(
        org_id=organization_id,
        team_id=team_id,
    )
    member_dtos = [serialize_team_membership(m) for m in members]
    return TeamDetailResponse(
        id=team.id,
        organization_id=team.organization_id,
        name=team.name,
        description=team.description,
        member_count=count,
        members=member_dtos,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.patch(
    "/{organization_id}/teams/{team_id}",
    response_model=TeamResponse,
    summary="Update Team",
)
async def update_team(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    request: TeamUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TeamResponse:
    """Update team details. Requires owner or admin role."""
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can update teams.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    team, count = await org_service.update_team(
        org_id=organization_id,
        team_id=team_id,
        request=request,
    )
    return TeamResponse(
        id=team.id,
        organization_id=team.organization_id,
        name=team.name,
        description=team.description,
        member_count=count,
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.delete(
    "/{organization_id}/teams/{team_id}",
    response_model=MessageResponse,
    summary="Delete Team",
)
async def delete_team(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """Delete a team. Requires owner or admin role."""
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can delete teams.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await org_service.delete_team(
        org_id=organization_id,
        team_id=team_id,
    )
    return MessageResponse(message="Team deleted successfully.")


@router.get(
    "/{organization_id}/teams/{team_id}/members",
    response_model=TeamMemberListResponse,
    summary="List Team Members",
)
async def list_team_members(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TeamMemberListResponse:
    """List members belonging to a team. Requires active membership."""
    await get_org_membership_for_user(organization_id, current_user.id, org_service)
    memberships = await org_service.list_team_members(
        org_id=organization_id,
        team_id=team_id,
    )
    items = [serialize_team_membership(m) for m in memberships]
    return TeamMemberListResponse(items=items, total=len(items))


@router.post(
    "/{organization_id}/teams/{team_id}/members",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Member to Team",
)
async def add_team_member(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    request: AddTeamMemberRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TeamMemberResponse:
    """
    Add an organization member to a team.
    Requires owner or admin role.
    Enforces workspace boundary: user must already be an active member
    of the organization.
    """
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can add members to teams.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    membership = await org_service.add_team_member(
        org_id=organization_id,
        team_id=team_id,
        request=request,
    )
    return serialize_team_membership(membership)


@router.patch(
    "/{organization_id}/teams/{team_id}/members/{user_id}",
    response_model=TeamMemberResponse,
    summary="Update Team Member Role",
)
async def update_team_member_role(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    request: UpdateTeamMemberRoleRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TeamMemberResponse:
    """Update role of a member in a team. Requires owner or admin role."""
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    if actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can modify team member roles.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    membership = await org_service.update_team_member(
        org_id=organization_id,
        team_id=team_id,
        user_id=user_id,
        request=request,
    )
    return serialize_team_membership(membership)


@router.delete(
    "/{organization_id}/teams/{team_id}/members/{user_id}",
    response_model=MessageResponse,
    summary="Remove Member from Team",
)
async def remove_team_member(
    organization_id: uuid.UUID,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """
    Remove a member from a team.
    Allowed for owner, admin, or the user themselves (leaving team).
    """
    actor_membership = await get_org_membership_for_user(
        organization_id, current_user.id, org_service
    )
    is_self = current_user.id == user_id
    if not is_self and actor_membership.role not in {"owner", "admin"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners or admins can remove members from teams.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await org_service.remove_team_member(
        org_id=organization_id,
        team_id=team_id,
        user_id=user_id,
    )
    return MessageResponse(message="Member removed successfully from team.")
