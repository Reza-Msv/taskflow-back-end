import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.projects.models import (
    Project,
    ProjectMembership,
    ProjectSequence,
)


class ProjectRepository:
    """Persistence operations for Project, ProjectMembership, and ProjectSequence entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(
        self, project_id: uuid.UUID, org_id: uuid.UUID | None = None
    ) -> Project | None:
        """Fetch project by ID, optionally scoped to organization."""
        stmt = select(Project).where(Project.id == project_id)
        if org_id is not None:
            stmt = stmt.where(Project.organization_id == org_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_key(self, org_id: uuid.UUID, key: str) -> Project | None:
        """Fetch project by unique uppercase key within an organization."""
        stmt = select(Project).where(
            Project.organization_id == org_id,
            Project.key == key.strip().upper(),
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def create_project(
        self,
        org_id: uuid.UUID,
        name: str,
        key: str,
        description: str | None = None,
        is_private: bool = False,
        owner_id: uuid.UUID | None = None,
    ) -> Project:
        """Create and persist a new project along with its sequence counter."""
        project = Project(
            organization_id=org_id,
            name=name.strip(),
            key=key.strip().upper(),
            description=description.strip() if description else None,
            is_private=is_private,
            owner_id=owner_id,
        )
        self.session.add(project)
        await self.session.flush()

        # Initialize issue sequence counter
        seq = ProjectSequence(
            project_id=project.id,
            current_sequence=0,
        )
        self.session.add(seq)
        await self.session.flush()

        return project

    async def update_project(
        self,
        project: Project,
        name: str | None = None,
        description: str | None = None,
        is_private: bool | None = None,
        is_archived: bool | None = None,
        owner_id: uuid.UUID | None = None,
    ) -> Project:
        """Update project metadata."""
        if name is not None:
            project.name = name.strip()
        if description is not None:
            project.description = description.strip() if description else None
        if is_private is not None:
            project.is_private = is_private
        if is_archived is not None:
            project.is_archived = is_archived
        if owner_id is not None:
            project.owner_id = owner_id
        await self.session.flush()
        return project

    async def list_projects_for_user(
        self,
        org_id: uuid.UUID,
        user_id: uuid.UUID,
        is_org_admin: bool = False,
        is_archived: bool | None = False,
        search: str | None = None,
        skip: int = 0,
        limit: int = 25,
    ) -> tuple[list[Project], int]:
        """
        List projects in an organization accessible to the user:
        - If org admin/owner: all projects in the org.
        - Otherwise: public projects OR projects where user is member/owner.
        """
        # User's project memberships in this org
        user_project_ids = (
            select(ProjectMembership.project_id)
            .join(Project, Project.id == ProjectMembership.project_id)
            .where(
                Project.organization_id == org_id,
                ProjectMembership.user_id == user_id,
            )
            .scalar_subquery()
        )

        stmt = select(Project).where(Project.organization_id == org_id)
        count_stmt = select(func.count(Project.id)).where(Project.organization_id == org_id)

        if is_archived is not None:
            stmt = stmt.where(Project.is_archived == is_archived)
            count_stmt = count_stmt.where(Project.is_archived == is_archived)

        if not is_org_admin:
            from sqlalchemy import or_

            access_filter = or_(
                Project.is_private == False,  # noqa: E712
                Project.owner_id == user_id,
                Project.id.in_(user_project_ids),
            )
            stmt = stmt.where(access_filter)
            count_stmt = count_stmt.where(access_filter)

        if search:
            search_pat = f"%{search.strip()}%"
            from sqlalchemy import or_

            search_filter = or_(
                Project.name.ilike(search_pat),
                Project.key.ilike(search_pat),
            )
            stmt = stmt.where(search_filter)
            count_stmt = count_stmt.where(search_filter)

        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = stmt.order_by(Project.created_at.desc()).offset(skip).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all()), total

    # ---------------- Project Membership operations ---------------- #

    async def get_membership(
        self, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> ProjectMembership | None:
        """Fetch project membership for a user."""
        stmt = (
            select(ProjectMembership)
            .options(selectinload(ProjectMembership.user))
            .where(
                ProjectMembership.project_id == project_id,
                ProjectMembership.user_id == user_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_memberships(
        self,
        project_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
    ) -> tuple[list[ProjectMembership], int]:
        """List members of a project with pagination."""
        count_stmt = select(func.count(ProjectMembership.id)).where(
            ProjectMembership.project_id == project_id
        )
        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = (
            select(ProjectMembership)
            .options(selectinload(ProjectMembership.user))
            .where(ProjectMembership.project_id == project_id)
            .order_by(ProjectMembership.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def add_membership(
        self,
        project_id: uuid.UUID,
        user_id: uuid.UUID,
        role: str = "member",
    ) -> ProjectMembership:
        """Add a user to a project."""
        membership = ProjectMembership(
            project_id=project_id,
            user_id=user_id,
            role=role,
        )
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def update_membership(
        self,
        membership: ProjectMembership,
        role: str,
    ) -> ProjectMembership:
        """Update role of a project member."""
        membership.role = role
        await self.session.flush()
        return membership

    async def delete_membership(
        self, project_id: uuid.UUID, user_id: uuid.UUID
    ) -> bool:
        """Remove a user from a project."""
        stmt = delete(ProjectMembership).where(
            ProjectMembership.project_id == project_id,
            ProjectMembership.user_id == user_id,
        )
        result = await self.session.execute(stmt)
        rowcount = getattr(result, "rowcount", 0)
        return bool(rowcount > 0)
