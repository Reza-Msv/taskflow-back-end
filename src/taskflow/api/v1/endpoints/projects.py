import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from taskflow.api.deps import (
    get_current_active_user,
    get_organization_service,
    get_project_service,
)
from taskflow.core.exceptions import DomainException
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import MessageResponse, UserResponse
from taskflow.domains.organizations.models import OrganizationMembership
from taskflow.domains.organizations.service import OrganizationService
from taskflow.domains.projects.models import Project, ProjectMembership
from taskflow.domains.projects.schemas import (
    AddProjectMembershipRequest,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectMembershipListResponse,
    ProjectMembershipResponse,
    ProjectResponse,
    ProjectUpdateRequest,
    UpdateProjectMembershipRoleRequest,
)
from taskflow.domains.projects.service import ProjectService

router = APIRouter(prefix="/organizations/{organization_id}/projects", tags=["Projects & Memberships"])


async def get_org_membership(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    org_service: OrganizationService,
) -> OrganizationMembership:
    """Enforce active organization membership."""
    membership = await org_service.org_repo.get_membership(organization_id, user_id)
    if membership is None or not membership.is_active:
        raise DomainException(
            code="FORBIDDEN",
            message="Access denied: You are not an active member of this organization.",
            status_code=status.HTTP_403_FORBIDDEN,
            details={"organization_id": str(organization_id)},
        )
    return membership


async def verify_project_access(
    project: Project,
    user_id: uuid.UUID,
    org_membership: OrganizationMembership,
    project_service: ProjectService,
) -> ProjectMembership | None:
    """
    Verify access to a project:
    - If user is org owner/admin, full access is granted.
    - If project is public, access is granted.
    - If project is private, user must be the project owner or explicit member.
    Returns ProjectMembership if found, else None.
    """
    is_org_admin = org_membership.role in {"owner", "admin"}
    proj_membership = await project_service.project_repo.get_membership(project.id, user_id)

    if is_org_admin or project.owner_id == user_id or proj_membership is not None:
        return proj_membership

    if not project.is_private:
        return None

    raise DomainException(
        code="PROJECT_NOT_FOUND",
        message="Project not found or inaccessible.",
        status_code=status.HTTP_404_NOT_FOUND,
        details={"project_id": str(project.id)},
    )


def can_manage_project(
    project: Project,
    user_id: uuid.UUID,
    org_membership: OrganizationMembership,
    proj_membership: ProjectMembership | None,
) -> bool:
    """Check if user can update or manage a project."""
    if org_membership.role in {"owner", "admin"}:
        return True
    if project.owner_id == user_id:
        return True
    if proj_membership is not None and proj_membership.role == "admin":
        return True
    return False


# -------------------------------------------------------------
# Projects CRUD Endpoints
# -------------------------------------------------------------


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Project",
)
async def create_project(
    organization_id: uuid.UUID,
    request: ProjectCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    """
    Create a new project in the organization.
    Requires member role or higher in the organization.
    """
    await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.create_project(
        org_id=organization_id,
        creator_id=current_user.id,
        request=request,
    )
    return ProjectResponse.model_validate(project)


@router.get(
    "",
    response_model=ProjectListResponse,
    summary="List Projects in Organization",
)
async def list_projects(
    organization_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    search: str | None = Query(None, description="Search by name or key"),
    is_archived: bool | None = Query(False, description="Filter by archive status"),
) -> ProjectListResponse:
    """List projects accessible to the user in this organization."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    is_org_admin = org_membership.role in {"owner", "admin"}

    projects, total = await project_service.list_projects(
        org_id=organization_id,
        user_id=current_user.id,
        is_org_admin=is_org_admin,
        is_archived=is_archived,
        search=search,
        page=page,
        page_size=page_size,
    )
    return ProjectListResponse(
        items=[ProjectResponse.model_validate(p) for p in projects],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Get Project Details",
)
async def get_project(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    """Retrieve project details. Validates tenant boundary and privacy."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    await verify_project_access(project, current_user.id, org_membership, project_service)
    return ProjectResponse.model_validate(project)


@router.patch(
    "/{project_id}",
    response_model=ProjectResponse,
    summary="Update Project Details",
)
async def update_project(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: ProjectUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectResponse:
    """Update project details or archive state. Requires org admin, project lead, or project admin."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    if not can_manage_project(project, current_user.id, org_membership, proj_membership):
        raise DomainException(
            code="FORBIDDEN",
            message="You do not have permission to modify this project.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    updated = await project_service.update_project(
        project_id=project_id,
        org_id=organization_id,
        request=request,
    )
    return ProjectResponse.model_validate(updated)


@router.delete(
    "/{project_id}",
    response_model=MessageResponse,
    summary="Delete Project",
)
async def delete_project(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
) -> MessageResponse:
    """Delete a project. Requires organization owner or admin role, or project lead."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)

    is_allowed = (
        org_membership.role in {"owner", "admin"}
        or project.owner_id == current_user.id
    )
    if not is_allowed:
        raise DomainException(
            code="FORBIDDEN",
            message="Only organization owners, admins, or project lead can delete a project.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await project_service.delete_project(project_id, org_id=organization_id)
    return MessageResponse(message="Project deleted successfully.")


# -------------------------------------------------------------
# Project Members Endpoints
# -------------------------------------------------------------


@router.get(
    "/{project_id}/members",
    response_model=ProjectMembershipListResponse,
    summary="List Project Members",
)
async def list_project_members(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
) -> ProjectMembershipListResponse:
    """List members of a project with pagination. Validates project access."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    await verify_project_access(project, current_user.id, org_membership, project_service)

    memberships, total = await project_service.list_members(
        project_id=project_id,
        org_id=organization_id,
        page=page,
        page_size=page_size,
    )
    items = []
    for m in memberships:
        user_dto = UserResponse.model_validate(m.user) if m.user else None
        items.append(
            ProjectMembershipResponse(
                id=m.id,
                project_id=m.project_id,
                user_id=m.user_id,
                role=m.role,
                created_at=m.created_at,
                updated_at=m.updated_at,
                user=user_dto,
            )
        )
    return ProjectMembershipListResponse(
        items=items, page=page, page_size=page_size, total=total
    )


@router.post(
    "/{project_id}/members",
    response_model=ProjectMembershipResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add Member to Project",
)
async def add_project_member(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: AddProjectMembershipRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectMembershipResponse:
    """
    Add an existing organization member to a project.
    Requires project manager permissions (org admin/owner, project lead, or project admin).
    Cannot add users outside the organization.
    """
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    if not can_manage_project(project, current_user.id, org_membership, proj_membership):
        raise DomainException(
            code="FORBIDDEN",
            message="You do not have permission to manage members of this project.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    membership = await project_service.add_member(
        project_id=project_id,
        org_id=organization_id,
        request=request,
    )
    user_dto = UserResponse.model_validate(membership.user) if membership.user else None
    return ProjectMembershipResponse(
        id=membership.id,
        project_id=membership.project_id,
        user_id=membership.user_id,
        role=membership.role,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
        user=user_dto,
    )


@router.patch(
    "/{project_id}/members/{user_id}",
    response_model=ProjectMembershipResponse,
    summary="Update Project Member Role",
)
async def update_project_member_role(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    request: UpdateProjectMembershipRoleRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectMembershipResponse:
    """Update role of a project member."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    if not can_manage_project(project, current_user.id, org_membership, proj_membership):
        raise DomainException(
            code="FORBIDDEN",
            message="You do not have permission to update member roles for this project.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    membership = await project_service.update_member_role(
        project_id=project_id,
        org_id=organization_id,
        user_id=user_id,
        request=request,
    )
    user_dto = UserResponse.model_validate(membership.user) if membership.user else None
    return ProjectMembershipResponse(
        id=membership.id,
        project_id=membership.project_id,
        user_id=membership.user_id,
        role=membership.role,
        created_at=membership.created_at,
        updated_at=membership.updated_at,
        user=user_dto,
    )


@router.delete(
    "/{project_id}/members/{user_id}",
    response_model=MessageResponse,
    summary="Remove Member from Project",
)
async def remove_project_member(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
) -> MessageResponse:
    """Remove member from project. User can remove themselves, or a project manager can remove them."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    is_self = current_user.id == user_id
    if not is_self and not can_manage_project(project, current_user.id, org_membership, proj_membership):
        raise DomainException(
            code="FORBIDDEN",
            message="You do not have permission to remove members from this project.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await project_service.remove_member(
        project_id=project_id,
        org_id=organization_id,
        user_id=user_id,
    )
    return MessageResponse(message="Member removed successfully from project.")
