import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from taskflow.domains.identity.schemas import UserResponse

OrganizationRole = Literal["owner", "admin", "member", "guest", "viewer"]


class OrganizationCreateRequest(BaseModel):
    """Payload for creating a new organization."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Organization display name",
        examples=["Acme Corp"],
    )
    slug: str = Field(
        ...,
        min_length=2,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="URL-safe unique organization identifier",
        examples=["acme-corp"],
    )


class OrganizationUpdateRequest(BaseModel):
    """Payload for updating organization details."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Organization display name",
    )
    is_active: bool | None = Field(
        default=None,
        description="Organization active status flag",
    )


class OrganizationResponse(BaseModel):
    """Organization details DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique organization identifier")
    name: str = Field(..., description="Organization display name")
    slug: str = Field(..., description="Unique slug")
    is_active: bool = Field(..., description="Whether the organization is active")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")


class OrganizationListResponse(BaseModel):
    """Paginated list of organizations."""

    items: list[OrganizationResponse] = Field(..., description="List of organizations")
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Number of items per page")
    total: int = Field(..., description="Total number of organizations")


class OrganizationMembershipResponse(BaseModel):
    """Organization membership DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Membership record ID")
    organization_id: uuid.UUID = Field(..., description="Organization ID")
    user_id: uuid.UUID = Field(..., description="User ID")
    role: str = Field(..., description="Role: owner, admin, member, guest, viewer")
    is_active: bool = Field(..., description="Membership active status")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")
    user: UserResponse | None = Field(default=None, description="Linked user details")


class OrganizationMembershipListResponse(BaseModel):
    """List of organization memberships with pagination."""

    items: list[OrganizationMembershipResponse] = Field(
        ..., description="List of memberships"
    )
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Number of items per page")
    total: int = Field(..., description="Total count")


class AddMembershipRequest(BaseModel):
    """Payload for adding a member to an organization."""

    user_id: uuid.UUID = Field(..., description="User ID to add")
    role: OrganizationRole = Field(
        default="member",
        description="Membership role: owner, admin, member, guest, viewer",
    )


class UpdateMembershipRoleRequest(BaseModel):
    """Payload for modifying a member's role or status."""

    role: OrganizationRole | None = Field(
        default=None,
        description="Updated role: owner, admin, member, guest, viewer",
    )
    is_active: bool | None = Field(
        default=None,
        description="Membership active status flag",
    )


class CreateInvitationRequest(BaseModel):
    """Payload for creating an organization membership invitation."""

    email: str = Field(
        ..., min_length=3, max_length=255, description="Invitee email address"
    )
    role: OrganizationRole = Field(
        default="member",
        description="Assigned role: owner, admin, member, guest, viewer",
    )


class InvitationResponse(BaseModel):
    """Invitation details DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique invitation identifier")
    organization_id: uuid.UUID = Field(..., description="Organization ID")
    email: str = Field(..., description="Invitee email address")
    role: str = Field(..., description="Assigned role")
    token: str = Field(..., description="Secure single-use token")
    invited_by_id: uuid.UUID | None = Field(
        default=None, description="Inviting user ID"
    )
    status: str = Field(
        default="pending",
        description="Invitation status: pending, accepted, expired, revoked",
    )
    expires_at: datetime = Field(..., description="Expiration timestamp (UTC)")
    accepted_at: datetime | None = Field(
        default=None, description="Acceptance timestamp (UTC)"
    )
    revoked_at: datetime | None = Field(
        default=None, description="Revocation timestamp (UTC)"
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")


class InvitationListResponse(BaseModel):
    """List of pending organization invitations."""

    items: list[InvitationResponse] = Field(..., description="List of invitations")
    total: int = Field(..., description="Total count")


class InvitationDetailResponse(BaseModel):
    """Public invitation verification DTO (does not expose sensitive system info)."""

    id: uuid.UUID = Field(..., description="Invitation ID")
    organization_id: uuid.UUID = Field(..., description="Organization ID")
    organization_name: str = Field(..., description="Organization display name")
    email: str = Field(..., description="Invited email address")
    role: str = Field(..., description="Role to be granted upon acceptance")
    invited_by_name: str | None = Field(
        default=None, description="Inviter display name"
    )
    status: str = Field(
        ..., description="Current status: pending, accepted, expired, revoked"
    )
    is_valid: bool = Field(..., description="Whether the invitation can be accepted")
    expires_at: datetime = Field(..., description="Expiration timestamp (UTC)")


class TransferOwnershipRequest(BaseModel):
    """Payload to transfer organization primary ownership to another member."""

    new_owner_id: uuid.UUID = Field(
        ...,
        description="User ID of member to become the new owner",
    )


class WorkspaceStatsResponse(BaseModel):
    """Statistical overview of an organization workspace."""

    organization_id: uuid.UUID = Field(..., description="Organization ID")
    member_count: int = Field(..., description="Total active members")
    team_count: int = Field(..., description="Total teams")
    project_count: int = Field(..., description="Total active projects")
    active_owners_count: int = Field(..., description="Active owners count")


# -------------------------------------------------------------
# Team Schemas
# -------------------------------------------------------------


class TeamCreateRequest(BaseModel):
    """Payload for creating a functional team within an organization."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Team display name",
        examples=["Frontend Engineering"],
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional team description or charter",
    )


class TeamUpdateRequest(BaseModel):
    """Payload for updating team attributes."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated team name",
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Updated team description",
    )


class AddTeamMemberRequest(BaseModel):
    """Payload for adding a workspace member to a team."""

    user_id: uuid.UUID = Field(
        ..., description="User ID (must belong to the organization)"
    )
    role: str = Field(
        default="member",
        min_length=1,
        max_length=50,
        description="Role in team (e.g. member, lead)",
    )


class UpdateTeamMemberRoleRequest(BaseModel):
    """Payload for modifying team member role."""

    role: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Updated team role (e.g. member, lead)",
    )


class TeamMemberResponse(BaseModel):
    """Team member association DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Team membership record ID")
    team_id: uuid.UUID = Field(..., description="Team ID")
    user_id: uuid.UUID = Field(..., description="User ID")
    role: str = Field(..., description="Role within team")
    created_at: datetime = Field(..., description="Added timestamp (UTC)")
    user: UserResponse | None = Field(default=None, description="Linked user details")


class TeamMemberListResponse(BaseModel):
    """List of members belonging to a team."""

    items: list[TeamMemberResponse] = Field(..., description="Team members")
    total: int = Field(..., description="Total member count")


class TeamResponse(BaseModel):
    """Team details DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Team ID")
    organization_id: uuid.UUID = Field(..., description="Organization ID")
    name: str = Field(..., description="Team name")
    description: str | None = Field(default=None, description="Team description")
    member_count: int = Field(default=0, description="Total members in team")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")


class TeamDetailResponse(TeamResponse):
    """Detailed team view including member records."""

    members: list[TeamMemberResponse] = Field(
        default_factory=list, description="List of team members"
    )


class TeamListResponse(BaseModel):
    """Paginated list of organization teams."""

    items: list[TeamResponse] = Field(..., description="List of teams")
    page: int = Field(..., description="Current page number (1-indexed)")
    page_size: int = Field(..., description="Items per page")
    total: int = Field(..., description="Total team count")
