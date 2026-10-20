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

    email: str = Field(..., min_length=3, max_length=255, description="Invitee email address")
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
    invited_by_id: uuid.UUID | None = Field(default=None, description="Inviting user ID")
    expires_at: datetime = Field(..., description="Expiration timestamp (UTC)")
    accepted_at: datetime | None = Field(default=None, description="Acceptance timestamp (UTC)")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")


class InvitationListResponse(BaseModel):
    """List of pending organization invitations."""

    items: list[InvitationResponse] = Field(..., description="List of invitations")
    total: int = Field(..., description="Total count")
