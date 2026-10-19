import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from taskflow.domains.identity.schemas import UserResponse

ProjectRole = Literal["admin", "member", "viewer"]


class ProjectCreateRequest(BaseModel):
    """Payload for creating a new project in an organization."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Project display name",
        examples=["TaskFlow Core"],
    )
    key: str = Field(
        ...,
        min_length=2,
        max_length=20,
        pattern=r"^[A-Z0-9]+$",
        description="Uppercase alphanumeric project issue key prefix (e.g. TF, CORE)",
        examples=["TF"],
    )
    description: str | None = Field(
        default=None,
        description="Optional extended project description",
    )
    is_private: bool = Field(
        default=False,
        description="Whether the project is private to assigned members",
    )


class ProjectUpdateRequest(BaseModel):
    """Payload for updating project metadata."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Project display name",
    )
    description: str | None = Field(
        default=None,
        description="Optional extended project description",
    )
    is_private: bool | None = Field(
        default=None,
        description="Whether the project is private to assigned members",
    )
    is_archived: bool | None = Field(
        default=None,
        description="Whether the project is archived",
    )
    owner_id: uuid.UUID | None = Field(
        default=None,
        description="Project lead / owner user ID",
    )


class ProjectResponse(BaseModel):
    """Project details DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique project identifier")
    organization_id: uuid.UUID = Field(..., description="Tenant organization ID")
    name: str = Field(..., description="Project name")
    key: str = Field(..., description="Project issue key prefix")
    description: str | None = Field(default=None, description="Extended description")
    is_private: bool = Field(..., description="Access visibility")
    is_archived: bool = Field(..., description="Soft archive flag")
    owner_id: uuid.UUID | None = Field(default=None, description="Project owner / lead user ID")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")


class ProjectListResponse(BaseModel):
    """Paginated list of projects."""

    items: list[ProjectResponse] = Field(..., description="List of projects")
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Number of items per page")
    total: int = Field(..., description="Total number of projects")


class ProjectMembershipResponse(BaseModel):
    """Project membership DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Project membership ID")
    project_id: uuid.UUID = Field(..., description="Project ID")
    user_id: uuid.UUID = Field(..., description="User ID")
    role: str = Field(..., description="Project role: admin, member, viewer")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")
    user: UserResponse | None = Field(default=None, description="Linked user details")


class ProjectMembershipListResponse(BaseModel):
    """List of project memberships."""

    items: list[ProjectMembershipResponse] = Field(..., description="List of project memberships")
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Number of items per page")
    total: int = Field(..., description="Total count")


class AddProjectMembershipRequest(BaseModel):
    """Payload for adding an organization member to a project."""

    user_id: uuid.UUID = Field(..., description="Organization member user ID to add to project")
    role: ProjectRole = Field(
        default="member",
        description="Project role: admin, member, viewer",
    )


class UpdateProjectMembershipRoleRequest(BaseModel):
    """Payload for updating project member role."""

    role: ProjectRole = Field(
        ...,
        description="Updated project role: admin, member, viewer",
    )
