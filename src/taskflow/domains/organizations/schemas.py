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
    """List of organization memberships."""

    items: list[OrganizationMembershipResponse] = Field(
        ..., description="List of memberships"
    )
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
