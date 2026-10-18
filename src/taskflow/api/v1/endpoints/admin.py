import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.api.deps import (
    get_auth_service,
    get_db,
    get_organization_service,
    require_superuser,
)
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import (
    MessageResponse,
    UserListResponse,
    UserResponse,
    UserUpdateAdminRequest,
)
from taskflow.domains.identity.service import AuthService
from taskflow.domains.organizations.models import Organization
from taskflow.domains.organizations.schemas import (
    AddMembershipRequest,
    OrganizationCreateRequest,
    OrganizationListResponse,
    OrganizationMembershipListResponse,
    OrganizationMembershipResponse,
    OrganizationResponse,
    OrganizationUpdateRequest,
    UpdateMembershipRoleRequest,
)
from taskflow.domains.organizations.service import OrganizationService
from taskflow.domains.projects.models import Project

router = APIRouter(prefix="/admin", tags=["Admin Foundation"])


# -------------------------------------------------------------
# Admin Overview & Metrics
# -------------------------------------------------------------


class AdminOverviewResponse(BaseModel):
    """Reliable aggregated system metrics for admin overview."""

    total_users: int = Field(..., description="Total registered users")
    active_users: int = Field(..., description="Total active users")
    total_organizations: int = Field(..., description="Total organizations")
    active_organizations: int = Field(..., description="Total active organizations")
    total_projects: int = Field(
        ..., description="Total projects created across organizations"
    )


@router.get(
    "/overview",
    response_model=AdminOverviewResponse,
    summary="Admin System Metrics Overview",
)
async def get_admin_overview(
    _: Annotated[User, Depends(require_superuser)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AdminOverviewResponse:
    """Provide a reliable summary of system-wide counts."""
    # Count total users
    total_users_res = await db.execute(select(func.count(User.id)))
    total_users = int(total_users_res.scalar_one() or 0)

    # Count active users
    active_users_res = await db.execute(
        select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
    )
    active_users = int(active_users_res.scalar_one() or 0)

    # Count total organizations
    total_orgs_res = await db.execute(select(func.count(Organization.id)))
    total_orgs = int(total_orgs_res.scalar_one() or 0)

    # Count active organizations
    active_orgs_res = await db.execute(
        select(func.count(Organization.id)).where(Organization.is_active == True)  # noqa: E712
    )
    active_orgs = int(active_orgs_res.scalar_one() or 0)

    # Count total projects
    total_projects_res = await db.execute(select(func.count(Project.id)))
    total_projects = int(total_projects_res.scalar_one() or 0)

    return AdminOverviewResponse(
        total_users=total_users,
        active_users=active_users,
        total_organizations=total_orgs,
        active_organizations=active_orgs,
        total_projects=total_projects,
    )


# -------------------------------------------------------------
# User Management Endpoints
# -------------------------------------------------------------


@router.get(
    "/users",
    response_model=UserListResponse,
    summary="List Users with Pagination & Filters",
)
async def list_users_admin(
    _: Annotated[User, Depends(require_superuser)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search by name or email"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    is_superuser: bool | None = Query(None, description="Filter by superuser status"),
) -> UserListResponse:
    """List users with pagination, search, and status filters."""
    users, total = await auth_service.list_users(
        page=page,
        page_size=page_size,
        search=search,
        is_active=is_active,
        is_superuser=is_superuser,
    )
    return UserListResponse(
        items=[UserResponse.model_validate(u) for u in users],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Get User Details",
)
async def get_user_admin(
    user_id: uuid.UUID,
    _: Annotated[User, Depends(require_superuser)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    """Retrieve detailed user profile by ID without exposing sensitive credentials."""
    user = await auth_service.get_user_by_id(user_id)
    return UserResponse.model_validate(user)


@router.patch(
    "/users/{user_id}",
    response_model=UserResponse,
    summary="Update User Profile / Status",
)
async def update_user_admin(
    user_id: uuid.UUID,
    request: UserUpdateAdminRequest,
    current_admin: Annotated[User, Depends(require_superuser)],
    auth_service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserResponse:
    """Update user profile, activate/deactivate account, or promote/demote superuser."""
    updated_user = await auth_service.update_user_by_admin(
        user_id=user_id,
        request=request,
        current_admin_id=current_admin.id,
    )
    return UserResponse.model_validate(updated_user)


# -------------------------------------------------------------
# Organization Management Endpoints
# -------------------------------------------------------------


@router.get(
    "/organizations",
    response_model=OrganizationListResponse,
    summary="List Organizations with Pagination",
)
async def list_organizations_admin(
    _: Annotated[User, Depends(require_superuser)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search by name or slug"),
    is_active: bool | None = Query(None, description="Filter by active status"),
) -> OrganizationListResponse:
    """List organizations with pagination, search, and status filters."""
    orgs, total = await org_service.list_organizations(
        page=page,
        page_size=page_size,
        search=search,
        is_active=is_active,
    )
    return OrganizationListResponse(
        items=[OrganizationResponse.model_validate(o) for o in orgs],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/organizations",
    response_model=OrganizationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Organization",
)
async def create_organization_admin(
    request: OrganizationCreateRequest,
    current_admin: Annotated[User, Depends(require_superuser)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationResponse:
    """Create a new organization and assign current admin as initial owner."""
    org = await org_service.create_organization(
        request=request,
        creator_id=current_admin.id,
    )
    return OrganizationResponse.model_validate(org)


@router.get(
    "/organizations/{organization_id}",
    response_model=OrganizationResponse,
    summary="Get Organization Details",
)
async def get_organization_admin(
    organization_id: uuid.UUID,
    _: Annotated[User, Depends(require_superuser)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationResponse:
    """Get organization details by ID."""
    org = await org_service.get_organization_by_id(organization_id)
    return OrganizationResponse.model_validate(org)


@router.patch(
    "/organizations/{organization_id}",
    response_model=OrganizationResponse,
    summary="Update Organization",
)
async def update_organization_admin(
    organization_id: uuid.UUID,
    request: OrganizationUpdateRequest,
    _: Annotated[User, Depends(require_superuser)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationResponse:
    """Update organization name or active status."""
    org = await org_service.update_organization(organization_id, request)
    return OrganizationResponse.model_validate(org)


# -------------------------------------------------------------
# Organization Memberships Endpoints
# -------------------------------------------------------------


@router.get(
    "/organizations/{organization_id}/members",
    response_model=OrganizationMembershipListResponse,
    summary="List Organization Members",
)
async def list_organization_members_admin(
    organization_id: uuid.UUID,
    _: Annotated[User, Depends(require_superuser)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationMembershipListResponse:
    """List all members and their roles for an organization."""
    memberships = await org_service.list_memberships(organization_id)
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
    return OrganizationMembershipListResponse(items=items, total=len(items))


@router.post(
    "/organizations/{organization_id}/members",
    response_model=OrganizationMembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Member to Organization",
)
async def add_organization_member_admin(
    organization_id: uuid.UUID,
    request: AddMembershipRequest,
    _: Annotated[User, Depends(require_superuser)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationMembershipResponse:
    """Add a user as a member to an organization with a specific role."""
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
    "/organizations/{organization_id}/members/{user_id}",
    response_model=OrganizationMembershipResponse,
    summary="Update Member Role or Status",
)
async def update_organization_member_admin(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    request: UpdateMembershipRoleRequest,
    _: Annotated[User, Depends(require_superuser)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> OrganizationMembershipResponse:
    """Update a user's role or membership active status in an organization."""
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
    "/organizations/{organization_id}/members/{user_id}",
    response_model=MessageResponse,
    summary="Remove Member from Organization",
)
async def remove_organization_member_admin(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    _: Annotated[User, Depends(require_superuser)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """
    Remove a member from an organization, protected against deleting
    the last active owner.
    """
    await org_service.remove_member(organization_id, user_id)
    return MessageResponse(message="Member removed successfully from organization.")
