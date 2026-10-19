import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.organizations.models import OrganizationMembership
from taskflow.domains.projects.models import Project, ProjectMembership
from taskflow.domains.work_items.models import (
    WorkItem,
    WorkItemActivity,
)

# Standard terminal / completed status categories or names
COMPLETED_STATUSES = {"done", "completed", "closed", "resolved"}
IN_PROGRESS_STATUSES = {"in_progress", "in-progress", "doing", "active"}


class DashboardRepository:
    """Repository executing tenant-isolated queries for user dashboard views."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def _accessible_projects_subquery(self, user_id: uuid.UUID):
        """
        Return a select statement yielding project IDs accessible to the user:
        1. Projects in organizations where the user is an active member, AND
           (project is public OR user has explicit ProjectMembership or is owner).
        """

        # Active organizations where user is a member
        user_orgs = (
            select(OrganizationMembership.organization_id)
            .where(
                OrganizationMembership.user_id == user_id,
                OrganizationMembership.is_active == True,  # noqa: E712
            )
            .scalar_subquery()
        )

        # Explicit project memberships
        proj_memberships = (
            select(ProjectMembership.project_id)
            .where(ProjectMembership.user_id == user_id)
            .scalar_subquery()
        )

        accessible_condition = and_(
            Project.organization_id.in_(user_orgs),
            Project.is_archived == False,  # noqa: E712
            or_(
                Project.is_private == False,  # noqa: E712
                Project.owner_id == user_id,
                Project.id.in_(proj_memberships),
            ),
        )
        return accessible_condition

    async def get_accessible_projects(
        self, user_id: uuid.UUID
    ) -> list[tuple[Project, str, int, int]]:
        """
        Fetch accessible projects with user's role and task counts.
        """

        # 1. Fetch accessible projects
        stmt = (
            select(
                Project,
                ProjectMembership.role.label("proj_role"),
                OrganizationMembership.role.label("org_role"),
            )
            .outerjoin(
                ProjectMembership,
                and_(
                    ProjectMembership.project_id == Project.id,
                    ProjectMembership.user_id == user_id,
                ),
            )
            .join(
                OrganizationMembership,
                and_(
                    OrganizationMembership.organization_id == Project.organization_id,
                    OrganizationMembership.user_id == user_id,
                    OrganizationMembership.is_active == True,  # noqa: E712
                ),
            )
            .where(
                Project.is_archived == False,  # noqa: E712
                or_(
                    Project.is_private == False,  # noqa: E712
                    Project.owner_id == user_id,
                    ProjectMembership.id.isnot(None),
                ),
            )
            .order_by(Project.name.asc())
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        if not rows:
            return []

        project_ids = [row[0].id for row in rows]

        # 2. Count total tasks and user assigned tasks per project
        stats_stmt = (
            select(
                WorkItem.project_id,
                func.count(WorkItem.id).label("total_tasks"),
                func.count(
                    case((WorkItem.assignee_id == user_id, WorkItem.id), else_=None)
                ).label("assigned_tasks"),
            )
            .where(WorkItem.project_id.in_(project_ids))
            .group_by(WorkItem.project_id)
        )
        stats_res = await self.session.execute(stats_stmt)
        stats_map = {row[0]: (row[1], row[2]) for row in stats_res.all()}

        summaries = []
        for project, proj_role, org_role in rows:
            # Determine effective role display
            effective_role = proj_role or org_role or "member"
            if project.owner_id == user_id:
                effective_role = "owner"

            total_t, assigned_t = stats_map.get(project.id, (0, 0))
            summaries.append((project, effective_role, total_t, assigned_t))

        return summaries

    async def get_user_task_metrics(
        self, user_id: uuid.UUID, now: datetime
    ) -> dict[str, Any]:
        """
        Calculate assigned task metrics:
        - counts per status
        - total assigned
        - in_progress count
        - completed count
        - overdue count
        """
        accessible_filter = self._accessible_projects_subquery(user_id)

        # Base query for tasks assigned to the user within accessible projects
        stmt = (
            select(
                WorkItem.status,
                func.count(WorkItem.id).label("status_count"),
                func.count(
                    case(
                        (
                            and_(
                                WorkItem.due_date.isnot(None),
                                WorkItem.due_date < now,
                                ~func.lower(WorkItem.status).in_(COMPLETED_STATUSES),
                            ),
                            WorkItem.id,
                        ),
                        else_=None,
                    )
                ).label("overdue_count"),
            )
            .join(Project, Project.id == WorkItem.project_id)
            .where(
                WorkItem.assignee_id == user_id,
                accessible_filter,
            )
            .group_by(WorkItem.status)
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        status_counts: dict[str, int] = {}
        total_assigned = 0
        in_progress_count = 0
        completed_count = 0
        overdue_count = 0

        for status_val, count, status_overdue in rows:
            status_counts[status_val] = count
            total_assigned += count
            overdue_count += status_overdue

            status_lower = status_val.lower()
            if status_lower in IN_PROGRESS_STATUSES:
                in_progress_count += count
            elif status_lower in COMPLETED_STATUSES:
                completed_count += count

        return {
            "status_counts": status_counts,
            "total_assigned": total_assigned,
            "in_progress_count": in_progress_count,
            "completed_count": completed_count,
            "overdue_count": overdue_count,
        }

    async def list_assigned_tasks(
        self,
        user_id: uuid.UUID,
        now: datetime,
        skip: int = 0,
        limit: int = 25,
        status: str | None = None,
        project_id: uuid.UUID | None = None,
        due_date_from: datetime | None = None,
        due_date_to: datetime | None = None,
        sort_by: str = "smart_order",
        sort_dir: str = "asc",
    ) -> tuple[list[WorkItem], int]:
        """
        List work items assigned to user with pagination, filters, and smart ordering.
        """

        accessible_filter = self._accessible_projects_subquery(user_id)

        conditions = [
            WorkItem.assignee_id == user_id,
            accessible_filter,
        ]

        if status:
            conditions.append(func.lower(WorkItem.status) == status.strip().lower())
        if project_id:
            conditions.append(WorkItem.project_id == project_id)
        if due_date_from:
            conditions.append(WorkItem.due_date >= due_date_from)
        if due_date_to:
            conditions.append(WorkItem.due_date <= due_date_to)

        # Count total
        count_stmt = (
            select(func.count(WorkItem.id))
            .join(Project, Project.id == WorkItem.project_id)
            .where(*conditions)
        )
        count_res = await self.session.execute(count_stmt)
        total = int(count_res.scalar_one() or 0)

        # Ordering
        order_clauses = []
        if sort_by == "smart_order":
            # Smart order:
            # 1. Overdue incomplete items first (due_date < now and not completed)
            # 2. Upcoming incomplete items with due dates (due_date >= now)
            # 3. Incomplete items without due date
            # 4. Completed items last
            is_completed = func.lower(WorkItem.status).in_(COMPLETED_STATUSES)
            is_overdue = and_(
                WorkItem.due_date.isnot(None),
                WorkItem.due_date < now,
                ~is_completed,
            )
            has_due = and_(WorkItem.due_date.isnot(None), ~is_completed)

            order_clauses.extend(
                [
                    case((is_completed, 4), (is_overdue, 1), (has_due, 2), else_=3),
                    WorkItem.due_date.asc().nulls_last(),
                    WorkItem.priority.desc(),
                    WorkItem.created_at.desc(),
                ]
            )
        elif sort_by == "due_date":
            if sort_dir.lower() == "desc":
                order_clauses.append(WorkItem.due_date.desc().nulls_last())
            else:
                order_clauses.append(WorkItem.due_date.asc().nulls_last())
        elif sort_by == "priority":
            # map priority order: urgent/highest/high -> medium -> low
            priority_rank = case(
                (func.lower(WorkItem.priority) == "urgent", 1),
                (func.lower(WorkItem.priority) == "high", 2),
                (func.lower(WorkItem.priority) == "medium", 3),
                (func.lower(WorkItem.priority) == "low", 4),
                else_=5,
            )
            if sort_dir.lower() == "desc":
                order_clauses.append(priority_rank.desc())
            else:
                order_clauses.append(priority_rank.asc())
        elif sort_by == "updated_at":
            if sort_dir.lower() == "desc":
                order_clauses.append(WorkItem.updated_at.desc())
            else:
                order_clauses.append(WorkItem.updated_at.asc())
        else:  # default to created_at
            if sort_dir.lower() == "asc":
                order_clauses.append(WorkItem.created_at.asc())
            else:
                order_clauses.append(WorkItem.created_at.desc())

        # Select items
        items_stmt = (
            select(WorkItem)
            .join(Project, Project.id == WorkItem.project_id)
            .options(selectinload(WorkItem.project))
            .where(*conditions)
            .order_by(*order_clauses)
            .offset(skip)
            .limit(limit)
        )
        items_res = await self.session.execute(items_stmt)
        items = list(items_res.scalars().all())

        return items, total

    async def get_overdue_and_upcoming_tasks(
        self,
        user_id: uuid.UUID,
        now: datetime,
        upcoming_days: int = 7,
        limit_per_group: int = 20,
    ) -> tuple[list[WorkItem], list[WorkItem]]:
        """
        Fetch:
        1. Overdue tasks: assigned to user, due_date < now, not completed.
        2. Upcoming tasks: assigned to user, approaching due date, not completed.
        """

        accessible_filter = self._accessible_projects_subquery(user_id)
        is_completed = func.lower(WorkItem.status).in_(COMPLETED_STATUSES)

        # Overdue tasks
        overdue_stmt = (
            select(WorkItem)
            .join(Project, Project.id == WorkItem.project_id)
            .options(selectinload(WorkItem.project))
            .where(
                WorkItem.assignee_id == user_id,
                WorkItem.due_date.isnot(None),
                WorkItem.due_date < now,
                ~is_completed,
                accessible_filter,
            )
            .order_by(WorkItem.due_date.asc())
            .limit(limit_per_group)
        )
        overdue_res = await self.session.execute(overdue_stmt)
        overdue_tasks = list(overdue_res.scalars().all())

        # Upcoming tasks
        upcoming_cutoff = now + timedelta(days=upcoming_days)
        upcoming_stmt = (
            select(WorkItem)
            .join(Project, Project.id == WorkItem.project_id)
            .options(selectinload(WorkItem.project))
            .where(
                WorkItem.assignee_id == user_id,
                WorkItem.due_date.isnot(None),
                WorkItem.due_date >= now,
                WorkItem.due_date <= upcoming_cutoff,
                ~is_completed,
                accessible_filter,
            )
            .order_by(WorkItem.due_date.asc())
            .limit(limit_per_group)
        )
        upcoming_res = await self.session.execute(upcoming_stmt)
        upcoming_tasks = list(upcoming_res.scalars().all())

        return overdue_tasks, upcoming_tasks

    async def list_recent_activity(
        self,
        user_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Retrieve genuine recent activity records related to the user:
        - Activities on work items assigned to the user or where the user is actor
        - Work item comments authored by or on work items assigned to the user
        Only within projects the user has access to.
        """
        accessible_filter = self._accessible_projects_subquery(user_id)

        # We query WorkItemActivity joined with WorkItem and User
        activity_stmt = (
            select(WorkItemActivity)
            .join(WorkItem, WorkItem.id == WorkItemActivity.work_item_id)
            .join(Project, Project.id == WorkItem.project_id)
            .options(
                selectinload(WorkItemActivity.user),
                selectinload(WorkItemActivity.work_item).selectinload(WorkItem.project),
            )
            .where(
                accessible_filter,
                or_(
                    WorkItem.assignee_id == user_id,
                    WorkItemActivity.user_id == user_id,
                ),
            )
            .order_by(WorkItemActivity.created_at.desc())
            .offset(skip)
            .limit(limit)
        )

        count_stmt = (
            select(func.count(WorkItemActivity.id))
            .join(WorkItem, WorkItem.id == WorkItemActivity.work_item_id)
            .join(Project, Project.id == WorkItem.project_id)
            .where(
                accessible_filter,
                or_(
                    WorkItem.assignee_id == user_id,
                    WorkItemActivity.user_id == user_id,
                ),
            )
        )

        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        acts_res = await self.session.execute(activity_stmt)
        activities = list(acts_res.scalars().all())

        results = []
        for act in activities:
            wi = act.work_item
            proj = wi.project if wi else None
            user_actor = act.user

            activity_type = "field_updated"
            details = f"Updated {act.field} from '{act.old_value}' to '{act.new_value}'"
            if act.field == "status":
                activity_type = "status_change"
                details = f"Changed status from '{act.old_value}' to '{act.new_value}'"
            elif act.field == "assignee_id":
                activity_type = "task_assignment"
                details = "Task assignment was modified"

            results.append(
                {
                    "id": act.id,
                    "activity_type": activity_type,
                    "field": act.field,
                    "old_value": act.old_value,
                    "new_value": act.new_value,
                    "created_at": act.created_at,
                    "work_item_id": wi.id if wi else None,
                    "work_item_key": wi.issue_key if wi else None,
                    "work_item_title": wi.title if wi else None,
                    "project_id": proj.id if proj else None,
                    "project_name": proj.name if proj else None,
                    "actor": (
                        {
                            "id": user_actor.id,
                            "email": user_actor.email,
                            "full_name": user_actor.full_name,
                        }
                        if user_actor
                        else None
                    ),
                    "details": details,
                }
            )

        return results, total
