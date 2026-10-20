import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.collaboration.models import WorkItemComment
from taskflow.domains.organizations.models import OrganizationMembership
from taskflow.domains.projects.models import Project, ProjectMembership, ProjectSequence
from taskflow.domains.work_items.models import (
    Attachment,
    ChecklistItem,
    SavedFilter,
    WorkflowStatus,
    WorkflowTransition,
    WorkItem,
    WorkItemActivity,
    work_item_labels,
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
        organization_id: uuid.UUID | None = None,
        priority: str | None = None,
        type: str | None = None,
        search: str | None = None,
        due_date_from: datetime | None = None,
        due_date_to: datetime | None = None,
        is_overdue: bool | None = None,
        due_soon: bool | None = None,
        is_completed: bool | None = None,
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
        if organization_id:
            conditions.append(WorkItem.organization_id == organization_id)
        if priority:
            conditions.append(func.lower(WorkItem.priority) == priority.strip().lower())
        if type:
            conditions.append(func.lower(WorkItem.type) == type.strip().lower())
        if due_date_from:
            conditions.append(WorkItem.due_date >= due_date_from)
        if due_date_to:
            conditions.append(WorkItem.due_date <= due_date_to)

        is_completed_expr = func.lower(WorkItem.status).in_(COMPLETED_STATUSES)
        if is_completed is not None:
            if is_completed:
                conditions.append(is_completed_expr)
            else:
                conditions.append(~is_completed_expr)

        if is_overdue is not None:
            overdue_cond = and_(
                WorkItem.due_date.isnot(None),
                WorkItem.due_date < now,
                ~is_completed_expr,
            )
            if is_overdue:
                conditions.append(overdue_cond)
            else:
                conditions.append(~overdue_cond)

        if due_soon is not None:
            seven_days = now + timedelta(days=7)
            soon_cond = and_(
                WorkItem.due_date.isnot(None),
                WorkItem.due_date >= now,
                WorkItem.due_date <= seven_days,
                ~is_completed_expr,
            )
            if due_soon:
                conditions.append(soon_cond)
            else:
                conditions.append(~soon_cond)

        if search:
            pat = f"%{search.strip()}%"
            conditions.append(
                or_(
                    WorkItem.title.ilike(pat),
                    WorkItem.issue_key.ilike(pat),
                    WorkItem.description.ilike(pat),
                )
            )

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
            completed_cond = func.lower(WorkItem.status).in_(COMPLETED_STATUSES)
            overdue_cond = and_(
                WorkItem.due_date.isnot(None),
                WorkItem.due_date < now,
                ~completed_cond,
            )
            has_due = and_(WorkItem.due_date.isnot(None), ~completed_cond)

            order_clauses.extend(
                [
                    case(
                        (completed_cond, 4),
                        (overdue_cond, 1),
                        (has_due, 2),
                        else_=3,
                    ),
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


class WorkItemRepository:
    """Persistence operations for WorkItem, WorkflowStatus, and WorkItemActivity."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(
        self, work_item_id: uuid.UUID, project_id: uuid.UUID | None = None
    ) -> WorkItem | None:
        """Fetch work item by primary key, optionally constrained to project."""
        stmt = (
            select(WorkItem)
            .options(
                selectinload(WorkItem.project),
                selectinload(WorkItem.assignee),
                selectinload(WorkItem.reporter),
            )
            .where(WorkItem.id == work_item_id)
        )
        if project_id is not None:
            stmt = stmt.where(WorkItem.project_id == project_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_next_sequence_and_issue_key(
        self, project_id: uuid.UUID, project_key: str
    ) -> tuple[int, str]:
        """
        Atomically increment the project sequence counter using SELECT ... FOR UPDATE.
        Guarantees gapless, collision-free issue keys under concurrent requests.
        """
        stmt = (
            select(ProjectSequence)
            .where(ProjectSequence.project_id == project_id)
            .with_for_update()
        )
        result = await self.session.execute(stmt)
        seq = result.scalar_one_or_none()
        if seq is None:
            # Fallback creation if not initialized
            seq = ProjectSequence(project_id=project_id, current_sequence=0)
            self.session.add(seq)
            await self.session.flush()

        seq.current_sequence += 1
        sequence_num = seq.current_sequence
        issue_key = f"{project_key}-{sequence_num}"
        return sequence_num, issue_key

    async def create_work_item(
        self,
        project_id: uuid.UUID,
        organization_id: uuid.UUID,
        sequence_number: int,
        issue_key: str,
        title: str,
        description: str | None = None,
        type: str = "task",
        priority: str = "medium",
        status: str = "todo",
        reporter_id: uuid.UUID | None = None,
        assignee_id: uuid.UUID | None = None,
        parent_id: uuid.UUID | None = None,
        due_date: datetime | None = None,
        estimated_points: float | None = None,
        rank: str = "0|hzzzzz:",
    ) -> WorkItem:
        """Create and persist a new work item."""
        item = WorkItem(
            project_id=project_id,
            organization_id=organization_id,
            sequence_number=sequence_number,
            issue_key=issue_key,
            title=title.strip(),
            description=description.strip() if description else None,
            type=type,
            priority=priority,
            status=status,
            reporter_id=reporter_id,
            assignee_id=assignee_id,
            parent_id=parent_id,
            due_date=due_date,
            estimated_points=estimated_points,
            rank=rank,
            version=1,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_work_items(
        self,
        project_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        status: str | None = None,
        assignee_id: uuid.UUID | None = None,
        reporter_id: uuid.UUID | None = None,
        priority: str | None = None,
        type: str | None = None,
        search: str | None = None,
        due_date_from: datetime | None = None,
        due_date_to: datetime | None = None,
        is_overdue: bool | None = None,
        due_soon: bool | None = None,
        is_completed: bool | None = None,
        label_id: uuid.UUID | None = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
        skip: int = 0,
        limit: int = 25,
        accessible_filter: Any = None,
    ) -> tuple[list[WorkItem], int]:
        """
        List work items with advanced filters, pagination, and deterministic
        sorting.
        """
        now = datetime.now(UTC)
        conditions = []

        if project_id is not None:
            conditions.append(WorkItem.project_id == project_id)
        if organization_id is not None:
            conditions.append(WorkItem.organization_id == organization_id)
        if accessible_filter is not None:
            conditions.append(accessible_filter)
        if status:
            conditions.append(func.lower(WorkItem.status) == status.strip().lower())
        if assignee_id is not None:
            conditions.append(WorkItem.assignee_id == assignee_id)
        if reporter_id is not None:
            conditions.append(WorkItem.reporter_id == reporter_id)
        if priority:
            conditions.append(func.lower(WorkItem.priority) == priority.strip().lower())
        if type:
            conditions.append(func.lower(WorkItem.type) == type.strip().lower())
        if due_date_from is not None:
            conditions.append(WorkItem.due_date >= due_date_from)
        if due_date_to is not None:
            conditions.append(WorkItem.due_date <= due_date_to)

        is_completed_expr = func.lower(WorkItem.status).in_(COMPLETED_STATUSES)
        if is_completed is not None:
            if is_completed:
                conditions.append(is_completed_expr)
            else:
                conditions.append(~is_completed_expr)

        if is_overdue is not None:
            overdue_cond = and_(
                WorkItem.due_date.isnot(None),
                WorkItem.due_date < now,
                ~is_completed_expr,
            )
            if is_overdue:
                conditions.append(overdue_cond)
            else:
                conditions.append(~overdue_cond)

        if due_soon is not None:
            seven_days = now + timedelta(days=7)
            soon_cond = and_(
                WorkItem.due_date.isnot(None),
                WorkItem.due_date >= now,
                WorkItem.due_date <= seven_days,
                ~is_completed_expr,
            )
            if due_soon:
                conditions.append(soon_cond)
            else:
                conditions.append(~soon_cond)

        if search:
            pat = f"%{search.strip()}%"
            conditions.append(
                or_(
                    WorkItem.title.ilike(pat),
                    WorkItem.issue_key.ilike(pat),
                    WorkItem.description.ilike(pat),
                )
            )

        if label_id is not None:
            label_subquery = (
                select(work_item_labels.c.work_item_id)
                .where(work_item_labels.c.label_id == label_id)
                .scalar_subquery()
            )
            conditions.append(WorkItem.id.in_(label_subquery))

        count_stmt = select(func.count(WorkItem.id))
        if accessible_filter is not None:
            count_stmt = count_stmt.join(Project, Project.id == WorkItem.project_id)
        if conditions:
            count_stmt = count_stmt.where(*conditions)

        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = select(WorkItem)
        if accessible_filter is not None:
            stmt = stmt.join(Project, Project.id == WorkItem.project_id)
        if conditions:
            stmt = stmt.where(*conditions)

        order_clauses = []
        is_desc = sort_dir.lower() == "desc"
        if sort_by == "smart_order":
            is_overdue_case = and_(
                WorkItem.due_date.isnot(None),
                WorkItem.due_date < now,
                ~is_completed_expr,
            )
            has_due_case = and_(WorkItem.due_date.isnot(None), ~is_completed_expr)
            order_clauses.extend(
                [
                    case(
                        (is_completed_expr, 4),
                        (is_overdue_case, 1),
                        (has_due_case, 2),
                        else_=3,
                    ),
                    WorkItem.due_date.asc().nulls_last(),
                    WorkItem.priority.desc(),
                    WorkItem.created_at.desc(),
                ]
            )
        elif sort_by == "due_date":
            order_clauses.append(
                WorkItem.due_date.desc().nulls_last()
                if is_desc
                else WorkItem.due_date.asc().nulls_last()
            )
        elif sort_by == "priority":
            order_clauses.append(
                WorkItem.priority.desc() if is_desc else WorkItem.priority.asc()
            )
        elif sort_by == "updated_at":
            order_clauses.append(
                WorkItem.updated_at.desc() if is_desc else WorkItem.updated_at.asc()
            )
        elif sort_by == "title":
            order_clauses.append(
                WorkItem.title.desc() if is_desc else WorkItem.title.asc()
            )
        elif sort_by == "rank":
            order_clauses.append(
                WorkItem.rank.desc() if is_desc else WorkItem.rank.asc()
            )
        else:
            order_clauses.append(
                WorkItem.created_at.desc() if is_desc else WorkItem.created_at.asc()
            )

        order_clauses.append(WorkItem.id.asc())

        stmt = (
            stmt.options(
                selectinload(WorkItem.assignee),
                selectinload(WorkItem.reporter),
                selectinload(WorkItem.project),
            )
            .order_by(*order_clauses)
            .offset(skip)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all()), total

    async def list_all_project_work_items(
        self, project_id: uuid.UUID
    ) -> list[WorkItem]:
        """List all work items for a project (e.g. for Kanban board)."""
        stmt = (
            select(WorkItem)
            .options(
                selectinload(WorkItem.assignee),
                selectinload(WorkItem.reporter),
            )
            .where(WorkItem.project_id == project_id)
            .order_by(WorkItem.rank.asc(), WorkItem.created_at.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def delete_work_item(self, item: WorkItem) -> None:
        """Delete a work item."""
        await self.session.delete(item)
        await self.session.flush()

    # ---------------- Workflow Statuses & Transitions ---------------- #

    async def get_project_statuses(self, project_id: uuid.UUID) -> list[WorkflowStatus]:
        """Get workflow statuses configured for a project, or system defaults."""
        stmt = (
            select(WorkflowStatus)
            .where(
                or_(
                    WorkflowStatus.project_id == project_id,
                    WorkflowStatus.project_id.is_(None),
                )
            )
            .order_by(WorkflowStatus.position.asc(), WorkflowStatus.created_at.asc())
        )
        res = await self.session.execute(stmt)
        statuses = list(res.scalars().all())
        # If project-specific statuses exist, prefer them over global defaults
        project_specific = [s for s in statuses if s.project_id == project_id]
        if project_specific:
            return project_specific
        return statuses

    async def get_status_by_name_or_id(
        self, project_id: uuid.UUID, identifier: str
    ) -> WorkflowStatus | None:
        """Find status matching UUID or case-insensitive name in project or system."""
        # Try UUID first
        try:
            status_uuid = uuid.UUID(identifier)
            stmt = select(WorkflowStatus).where(
                WorkflowStatus.id == status_uuid,
                or_(
                    WorkflowStatus.project_id == project_id,
                    WorkflowStatus.project_id.is_(None),
                ),
            )
            res = await self.session.execute(stmt)
            found = res.scalar_one_or_none()
            if found:
                return found
        except ValueError:
            pass

        # Match name or category or normalized name
        norm = identifier.strip().lower()
        stmt = select(WorkflowStatus).where(
            or_(
                func.lower(WorkflowStatus.name) == norm,
                func.lower(WorkflowStatus.name) == norm.replace("_", " "),
                func.lower(WorkflowStatus.category) == norm,
            ),
            or_(
                WorkflowStatus.project_id == project_id,
                WorkflowStatus.project_id.is_(None),
            ),
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_allowed_transitions(
        self, from_status_id: uuid.UUID
    ) -> list[WorkflowTransition]:
        """Fetch allowed transitions from a given status."""
        stmt = select(WorkflowTransition).where(
            WorkflowTransition.from_status_id == from_status_id
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def has_configured_transitions(self, project_id: uuid.UUID) -> bool:
        """Check if any explicit workflow transitions exist for project statuses."""
        stmt = (
            select(func.count(WorkflowTransition.id))
            .join(
                WorkflowStatus,
                WorkflowStatus.id == WorkflowTransition.from_status_id,
            )
            .where(
                or_(
                    WorkflowStatus.project_id == project_id,
                    WorkflowStatus.project_id.is_(None),
                )
            )
        )
        res = await self.session.execute(stmt)
        return int(res.scalar_one() or 0) > 0

    async def ensure_default_workflow_statuses(
        self, project_id: uuid.UUID
    ) -> list[WorkflowStatus]:
        """Initialize standard workflow statuses for a project if none exist."""
        existing = await self.get_project_statuses(project_id)
        if existing:
            return existing

        defaults = [
            ("To Do", "todo", 1, True),
            ("In Progress", "in_progress", 2, False),
            ("Done", "done", 3, False),
        ]
        created = []
        for name, category, pos, is_def in defaults:
            status_obj = WorkflowStatus(
                project_id=project_id,
                name=name,
                category=category,
                position=pos,
                is_default=is_def,
            )
            self.session.add(status_obj)
            created.append(status_obj)

        await self.session.flush()
        return created

    # ---------------- Task History & Activity Logging ---------------- #

    async def record_activity(
        self,
        work_item_id: uuid.UUID,
        user_id: uuid.UUID | None,
        field: str,
        old_value: str | None = None,
        new_value: str | None = None,
    ) -> WorkItemActivity:
        """Append an auditable history event for a work item."""
        activity = WorkItemActivity(
            work_item_id=work_item_id,
            user_id=user_id,
            field=field,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
        )
        self.session.add(activity)
        await self.session.flush()
        return activity

    async def list_activities_for_task(
        self,
        work_item_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
    ) -> tuple[list[WorkItemActivity], int]:
        """List paginated audit activities for a specific work item."""
        count_stmt = select(func.count(WorkItemActivity.id)).where(
            WorkItemActivity.work_item_id == work_item_id
        )
        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = (
            select(WorkItemActivity)
            .options(selectinload(WorkItemActivity.user))
            .where(WorkItemActivity.work_item_id == work_item_id)
            .order_by(WorkItemActivity.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all()), total

    async def get_task_details(
        self, work_item_id: uuid.UUID, project_id: uuid.UUID | None = None
    ) -> WorkItem | None:
        """Fetch full work item details with all collaboration relationships loaded."""
        stmt = (
            select(WorkItem)
            .options(
                selectinload(WorkItem.project),
                selectinload(WorkItem.assignee),
                selectinload(WorkItem.reporter),
                selectinload(WorkItem.checklists).selectinload(ChecklistItem.assignee),
                selectinload(WorkItem.attachments).selectinload(Attachment.uploader),
                selectinload(WorkItem.children).selectinload(WorkItem.assignee),
                selectinload(WorkItem.comments).selectinload(WorkItemComment.author),
            )
            .where(WorkItem.id == work_item_id)
        )
        if project_id is not None:
            stmt = stmt.where(WorkItem.project_id == project_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    # -------------------------------------------------------------
    # Checklists
    # -------------------------------------------------------------

    async def create_checklist_item(
        self,
        work_item_id: uuid.UUID,
        title: str,
        assignee_id: uuid.UUID | None = None,
        position: int | None = None,
    ) -> ChecklistItem:
        """Create a new checklist item under a task."""
        if position is None:
            max_pos_stmt = select(
                func.coalesce(func.max(ChecklistItem.position), -1)
            ).where(ChecklistItem.work_item_id == work_item_id)
            max_pos = (await self.session.execute(max_pos_stmt)).scalar() or -1
            position = max_pos + 1

        item = ChecklistItem(
            work_item_id=work_item_id,
            title=title,
            assignee_id=assignee_id,
            position=position,
            is_completed=False,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def get_checklist_item_by_id(
        self,
        item_id: uuid.UUID,
        work_item_id: uuid.UUID | None = None,
    ) -> ChecklistItem | None:
        """Get checklist item with assignee eagerly loaded."""
        stmt = (
            select(ChecklistItem)
            .options(selectinload(ChecklistItem.assignee))
            .where(ChecklistItem.id == item_id)
        )
        if work_item_id is not None:
            stmt = stmt.where(ChecklistItem.work_item_id == work_item_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_checklist_items(
        self, work_item_id: uuid.UUID
    ) -> list[ChecklistItem]:
        """Fetch all checklist items for a task ordered by position."""
        stmt = (
            select(ChecklistItem)
            .options(selectinload(ChecklistItem.assignee))
            .where(ChecklistItem.work_item_id == work_item_id)
            .order_by(ChecklistItem.position.asc(), ChecklistItem.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_checklist_item(self, item: ChecklistItem) -> None:
        """Delete checklist item."""
        await self.session.delete(item)
        await self.session.flush()

    # -------------------------------------------------------------
    # Attachments
    # -------------------------------------------------------------

    async def create_attachment(
        self,
        work_item_id: uuid.UUID,
        uploader_id: uuid.UUID | None,
        file_name: str,
        file_size: int,
        content_type: str,
        storage_path: str,
    ) -> Attachment:
        """Create attachment record for a work item."""
        attachment = Attachment(
            work_item_id=work_item_id,
            uploader_id=uploader_id,
            file_name=file_name,
            file_size=file_size,
            content_type=content_type,
            storage_path=storage_path,
        )
        self.session.add(attachment)
        await self.session.flush()
        return attachment

    async def get_attachment_by_id(
        self,
        attachment_id: uuid.UUID,
        work_item_id: uuid.UUID | None = None,
    ) -> Attachment | None:
        """Get attachment metadata with uploader eagerly loaded."""
        stmt = (
            select(Attachment)
            .options(selectinload(Attachment.uploader))
            .where(Attachment.id == attachment_id)
        )
        if work_item_id is not None:
            stmt = stmt.where(Attachment.work_item_id == work_item_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_attachments(self, work_item_id: uuid.UUID) -> list[Attachment]:
        """Fetch all attachments for a work item."""
        stmt = (
            select(Attachment)
            .options(selectinload(Attachment.uploader))
            .where(Attachment.work_item_id == work_item_id)
            .order_by(Attachment.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_attachment(self, attachment: Attachment) -> None:
        """Delete attachment record."""
        await self.session.delete(attachment)
        await self.session.flush()

    # -------------------------------------------------------------
    # Subtasks & Hierarchy Validation
    # -------------------------------------------------------------

    async def list_subtasks(
        self,
        parent_id: uuid.UUID,
        skip: int = 0,
        limit: int = 25,
    ) -> tuple[list[WorkItem], int]:
        """List subtasks for a parent task with pagination."""
        count_stmt = select(func.count(WorkItem.id)).where(
            WorkItem.parent_id == parent_id
        )
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(WorkItem)
            .options(
                selectinload(WorkItem.assignee),
                selectinload(WorkItem.reporter),
            )
            .where(WorkItem.parent_id == parent_id)
            .order_by(WorkItem.sequence_number.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all()), total

    async def check_circular_parent(
        self,
        task_id: uuid.UUID,
        candidate_parent_id: uuid.UUID,
        max_depth: int = 20,
    ) -> bool:
        """
        Check if candidate_parent_id as parent of task_id causes a cycle.
        Returns True if a cycle is detected, False otherwise.
        """
        current_id: uuid.UUID | None = candidate_parent_id
        depth = 0
        while current_id is not None and depth < max_depth:
            if current_id == task_id:
                return True
            parent_stmt = select(WorkItem.parent_id).where(WorkItem.id == current_id)
            res = await self.session.execute(parent_stmt)
            current_id = res.scalar()
            depth += 1
        return False

    async def search_tasks(
        self,
        query: str,
        accessible_filter: Any,
        organization_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[tuple[WorkItem, str]], int]:
        """Search accessible tasks matching title, description, or issue key."""
        pat = f"%{query.strip()}%"
        matched_field_case = case(
            (WorkItem.issue_key.ilike(pat), "issue_key"),
            (WorkItem.title.ilike(pat), "title"),
            else_="description",
        ).label("matched_field")

        search_filter = or_(
            WorkItem.issue_key.ilike(pat),
            WorkItem.title.ilike(pat),
            WorkItem.description.ilike(pat),
        )

        conditions = [
            accessible_filter,
            search_filter,
        ]
        if organization_id is not None:
            conditions.append(WorkItem.organization_id == organization_id)
        if project_id is not None:
            conditions.append(WorkItem.project_id == project_id)

        count_stmt = (
            select(func.count(WorkItem.id))
            .join(Project, Project.id == WorkItem.project_id)
            .where(*conditions)
        )
        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = (
            select(WorkItem, matched_field_case)
            .join(Project, Project.id == WorkItem.project_id)
            .options(
                selectinload(WorkItem.project),
            )
            .where(*conditions)
            .order_by(WorkItem.created_at.desc(), WorkItem.id.asc())
            .offset(offset)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        rows = res.all()
        return [(item, matched_field) for item, matched_field in rows], total

    async def search_projects(
        self,
        query: str,
        accessible_filter: Any,
        organization_id: uuid.UUID | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[tuple[Project, str]], int]:
        """Search accessible projects matching name, key, or description."""
        pat = f"%{query.strip()}%"
        matched_field_case = case(
            (Project.key.ilike(pat), "key"),
            (Project.name.ilike(pat), "name"),
            else_="description",
        ).label("matched_field")

        search_filter = or_(
            Project.key.ilike(pat),
            Project.name.ilike(pat),
            Project.description.ilike(pat),
        )

        conditions = [
            accessible_filter,
            search_filter,
        ]
        if organization_id is not None:
            conditions.append(Project.organization_id == organization_id)

        count_stmt = select(func.count(Project.id)).where(*conditions)
        total_res = await self.session.execute(count_stmt)
        total = int(total_res.scalar_one() or 0)

        stmt = (
            select(Project, matched_field_case)
            .where(*conditions)
            .order_by(Project.name.asc(), Project.id.asc())
            .offset(offset)
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        rows = res.all()
        return [(proj, matched_field) for proj, matched_field in rows], total


class SavedFilterRepository:
    """Persistence operations for saved filters/views."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, saved_filter: SavedFilter) -> SavedFilter:
        self.session.add(saved_filter)
        await self.session.flush()
        return saved_filter

    async def get_by_id(self, filter_id: uuid.UUID) -> SavedFilter | None:
        stmt = select(SavedFilter).where(SavedFilter.id == filter_id)
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def list_for_user(
        self,
        user_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> list[SavedFilter]:
        stmt = select(SavedFilter).where(SavedFilter.user_id == user_id)
        if organization_id is not None:
            stmt = stmt.where(
                or_(
                    SavedFilter.organization_id == organization_id,
                    SavedFilter.organization_id.is_(None),
                )
            )
        if project_id is not None:
            stmt = stmt.where(
                or_(
                    SavedFilter.project_id == project_id,
                    SavedFilter.project_id.is_(None),
                )
            )
        stmt = stmt.order_by(
            SavedFilter.is_favorite.desc(),
            SavedFilter.name.asc(),
            SavedFilter.created_at.desc(),
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def update(self, saved_filter: SavedFilter) -> SavedFilter:
        saved_filter.updated_at = datetime.now(UTC)
        await self.session.flush()
        return saved_filter

    async def delete(self, saved_filter: SavedFilter) -> None:
        await self.session.delete(saved_filter)
        await self.session.flush()

