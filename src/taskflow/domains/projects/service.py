import uuid

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.exceptions import DomainException
from taskflow.domains.identity.repository import UserRepository
from taskflow.domains.organizations.repository import OrganizationRepository
from taskflow.domains.projects.models import Project, ProjectMembership
from taskflow.domains.projects.repository import ProjectRepository
from taskflow.domains.projects.schemas import (
    AddProjectMembershipRequest,
    ProjectCreateRequest,
    ProjectUpdateRequest,
    UpdateProjectMembershipRoleRequest,
)


class ProjectService:
    """Service managing projects and project memberships with strict tenant and role authorization."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.project_repo = ProjectRepository(session)
        self.org_repo = OrganizationRepository(session)
        self.user_repo = UserRepository(session)

    async def get_project_by_id(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID | None = None,
    ) -> Project:
        """Fetch project or raise PROJECT_NOT_FOUND."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found or inaccessible.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"project_id": str(project_id)},
            )
        return project

    async def list_projects(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        is_org_admin: bool = False,
        is_archived: bool | None = False,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[Project], int]:
        """List accessible projects within an organization for the authenticated user."""
        skip = (page - 1) * page_size
        return await self.project_repo.list_projects_for_user(
            org_id=org_id,
            user_id=user_id,
            is_org_admin=is_org_admin,
            is_archived=is_archived,
            search=search,
            skip=skip,
            limit=page_size,
        )

    async def create_project(
        self,
        org_id: uuid.UUID,
        creator_id: uuid.UUID,
        request: ProjectCreateRequest,
    ) -> Project:
        """Create a new project in the organization, making creator the owner and admin."""
        # Ensure project key is unique within this organization
        existing = await self.project_repo.get_by_key(org_id, request.key)
        if existing is not None:
            raise DomainException(
                code="PROJECT_KEY_EXISTS",
                message=f"Project with key '{request.key.upper()}' already exists in this organization.",
                status_code=status.HTTP_409_CONFLICT,
                details={"organization_id": str(org_id), "key": request.key.upper()},
            )

        project = await self.project_repo.create_project(
            org_id=org_id,
            name=request.name,
            key=request.key,
            description=request.description,
            is_private=request.is_private,
            owner_id=creator_id,
        )

        # Automatically add creator as project admin
        await self.project_repo.add_membership(
            project_id=project.id,
            user_id=creator_id,
            role="admin",
        )

        await self.session.commit()
        await self.session.refresh(project)
        return project

    async def update_project(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        request: ProjectUpdateRequest,
    ) -> Project:
        """Update project details or archive state."""
        project = await self.get_project_by_id(project_id, org_id=org_id)

        # If updating owner_id, ensure new owner is an active member of the organization
        if request.owner_id is not None:
            owner_membership = await self.org_repo.get_membership(
                org_id, request.owner_id
            )
            if owner_membership is None or not owner_membership.is_active:
                raise DomainException(
                    code="USER_NOT_IN_ORGANIZATION",
                    message="Proposed project owner must be an active member of the organization.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"user_id": str(request.owner_id)},
                )

        updated = await self.project_repo.update_project(
            project=project,
            name=request.name,
            description=request.description,
            is_private=request.is_private,
            is_archived=request.is_archived,
            owner_id=request.owner_id,
        )
        await self.session.commit()
        await self.session.refresh(updated)
        return updated

    async def delete_project(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> None:
        """Hard delete a project and cascades (use with caution)."""
        project = await self.get_project_by_id(project_id, org_id=org_id)
        await self.session.delete(project)
        await self.session.commit()

    # ---------------- Project Memberships ---------------- #

    async def list_members(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[ProjectMembership], int]:
        """List members of a project with pagination."""
        await self.get_project_by_id(project_id, org_id=org_id)
        skip = (page - 1) * page_size
        return await self.project_repo.list_memberships(
            project_id=project_id,
            skip=skip,
            limit=page_size,
        )

    async def add_member(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        request: AddProjectMembershipRequest,
    ) -> ProjectMembership:
        """Add an organization member to a project."""
        await self.get_project_by_id(project_id, org_id=org_id)

        # Requirement: Prevent assigning users who do not belong to the organization
        org_membership = await self.org_repo.get_membership(org_id, request.user_id)
        if org_membership is None or not org_membership.is_active:
            raise DomainException(
                code="USER_NOT_IN_ORGANIZATION",
                message="Cannot add user to project: user is not an active member of this organization.",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "organization_id": str(org_id),
                    "user_id": str(request.user_id),
                },
            )

        existing_membership = await self.project_repo.get_membership(
            project_id, request.user_id
        )
        if existing_membership is not None:
            raise DomainException(
                code="PROJECT_MEMBERSHIP_ALREADY_EXISTS",
                message="User is already a member of this project.",
                status_code=status.HTTP_409_CONFLICT,
                details={
                    "project_id": str(project_id),
                    "user_id": str(request.user_id),
                },
            )

        membership = await self.project_repo.add_membership(
            project_id=project_id,
            user_id=request.user_id,
            role=request.role,
        )
        await self.session.commit()
        fresh = await self.project_repo.get_membership(project_id, request.user_id)
        return fresh or membership

    async def update_member_role(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        request: UpdateProjectMembershipRoleRequest,
    ) -> ProjectMembership:
        """Update role of a project member."""
        await self.get_project_by_id(project_id, org_id=org_id)

        membership = await self.project_repo.get_membership(project_id, user_id)
        if membership is None:
            raise DomainException(
                code="PROJECT_MEMBERSHIP_NOT_FOUND",
                message="User is not a member of this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"project_id": str(project_id), "user_id": str(user_id)},
            )

        updated = await self.project_repo.update_membership(
            membership=membership,
            role=request.role,
        )
        await self.session.commit()
        fresh = await self.project_repo.get_membership(project_id, user_id)
        return fresh or updated

    async def remove_member(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Remove a member from a project."""
        await self.get_project_by_id(project_id, org_id=org_id)

        membership = await self.project_repo.get_membership(project_id, user_id)
        if membership is None:
            raise DomainException(
                code="PROJECT_MEMBERSHIP_NOT_FOUND",
                message="User is not a member of this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"project_id": str(project_id), "user_id": str(user_id)},
            )

        await self.project_repo.delete_membership(project_id, user_id)
        await self.session.commit()
