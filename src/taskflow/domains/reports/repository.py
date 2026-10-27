import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.projects.models import ProjectMembership
from taskflow.domains.work_items.models import (
    WorkflowStatus,
    WorkItem,
    WorkItemActivity,
)
from taskflow.domains.work_items.repository import COMPLETED_STATUSES

IN_PROGRESS_DEFAULT_STATUSES = {
    "in_progress",
    "in progress",
    "in review",
    "in_review",
    "review",
    "doing",
}


class ReportRepository:
    """Efficient aggregate SQL querying for project analytics and reports."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_project_workflow_statuses(
        self, project_id: uuid.UUID
    ) -> list[WorkflowStatus]:
        """Retrieve project workflow statuses ordered by position."""
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
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_overview_task_aggregates(
        self,
        project_id: uuid.UUID,
        now_utc: datetime,
        due_window_end: datetime,
    ) -> dict:
        """
        Compute high-level task metrics in a single database query.
        Returns total, completed, overdue, unassigned, and points metrics.
        """
        is_completed_cond = func.lower(WorkItem.status).in_(COMPLETED_STATUSES)
        is_overdue_cond = and_(
            WorkItem.due_date.is_not(None),
            WorkItem.due_date < now_utc,
            ~is_completed_cond,
        )
        is_unassigned_cond = and_(
            WorkItem.assignee_id.is_(None),
            ~is_completed_cond,
        )
        is_due_soon_cond = and_(
            WorkItem.due_date.is_not(None),
            WorkItem.due_date >= now_utc,
            WorkItem.due_date <= due_window_end,
            ~is_completed_cond,
        )

        stmt = select(
            func.count(WorkItem.id).label("total_tasks"),
            func.count(case((is_completed_cond, 1))).label("completed_tasks"),
            func.count(case((is_overdue_cond, 1))).label("overdue_tasks"),
            func.count(case((is_unassigned_cond, 1))).label("unassigned_tasks"),
            func.count(case((is_due_soon_cond, 1))).label("due_soon_tasks"),
            func.coalesce(func.sum(WorkItem.estimated_points), 0.0).label(
                "total_points"
            ),
            func.coalesce(
                func.sum(case((is_completed_cond, WorkItem.estimated_points))), 0.0
            ).label("completed_points"),
        ).where(WorkItem.project_id == project_id)

        res = await self.session.execute(stmt)
        row = res.one()

        return {
            "total_tasks": row.total_tasks or 0,
            "completed_tasks": row.completed_tasks or 0,
            "overdue_tasks": row.overdue_tasks or 0,
            "unassigned_tasks": row.unassigned_tasks or 0,
            "due_soon_tasks": row.due_soon_tasks or 0,
            "total_points": float(row.total_points or 0.0),
            "completed_points": float(row.completed_points or 0.0),
        }

    async def get_distribution_by_status(
        self, project_id: uuid.UUID
    ) -> list[tuple[str, int]]:
        """Group and count tasks by status name."""
        stmt = (
            select(WorkItem.status, func.count(WorkItem.id))
            .where(WorkItem.project_id == project_id)
            .group_by(WorkItem.status)
        )
        res = await self.session.execute(stmt)
        return [(status_name, count) for status_name, count in res.all()]

    async def get_distribution_by_priority(
        self, project_id: uuid.UUID
    ) -> list[tuple[str, int]]:
        """Group and count tasks by priority."""
        stmt = (
            select(WorkItem.priority, func.count(WorkItem.id))
            .where(WorkItem.project_id == project_id)
            .group_by(WorkItem.priority)
        )
        res = await self.session.execute(stmt)
        return [(priority, count) for priority, count in res.all()]

    async def get_distribution_by_type(
        self, project_id: uuid.UUID
    ) -> list[tuple[str, int]]:
        """Group and count tasks by work item type."""
        stmt = (
            select(WorkItem.type, func.count(WorkItem.id))
            .where(WorkItem.project_id == project_id)
            .group_by(WorkItem.type)
        )
        res = await self.session.execute(stmt)
        return [(task_type, count) for task_type, count in res.all()]

    async def get_completed_tasks_in_range(
        self,
        project_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
    ) -> list[tuple[datetime, float]]:
        """
        Fetch completion timestamp and story points for tasks completed in range.
        Prioritizes WorkItem.completed_at; if null, falls back to latest status.
        """
        # Primary query using completed_at
        stmt = (
            select(
                WorkItem.id,
                WorkItem.completed_at,
                func.coalesce(WorkItem.estimated_points, 0.0),
            )
            .where(
                WorkItem.project_id == project_id,
                WorkItem.completed_at.is_not(None),
                WorkItem.completed_at >= start_date,
                WorkItem.completed_at <= end_date,
            )
            .order_by(WorkItem.completed_at.asc())
        )
        res = await self.session.execute(stmt)
        primary_rows = [
            (comp_at, float(pts)) for _, comp_at, pts in res.all() if comp_at
        ]

        # Check if tasks completed without completed_at populated (e.g. historical data)
        activity_stmt = (
            select(
                WorkItemActivity.work_item_id,
                func.max(WorkItemActivity.created_at).label("act_completed_at"),
                func.coalesce(WorkItem.estimated_points, 0.0),
            )
            .join(WorkItem, WorkItem.id == WorkItemActivity.work_item_id)
            .where(
                WorkItem.project_id == project_id,
                WorkItem.completed_at.is_(None),
                func.lower(WorkItem.status).in_(COMPLETED_STATUSES),
                WorkItemActivity.field == "status",
                func.lower(WorkItemActivity.new_value).in_(COMPLETED_STATUSES),
                WorkItemActivity.created_at >= start_date,
                WorkItemActivity.created_at <= end_date,
            )
            .group_by(WorkItemActivity.work_item_id, WorkItem.estimated_points)
        )
        act_res = await self.session.execute(activity_stmt)
        activity_rows = [
            (act_time, float(pts)) for _, act_time, pts in act_res.all() if act_time
        ]

        combined = primary_rows + activity_rows
        combined.sort(key=lambda x: x[0])
        return combined

    async def get_project_members_with_users(
        self, project_id: uuid.UUID
    ) -> list[ProjectMembership]:
        """Fetch all project memberships with eager-loaded user entities."""
        stmt = (
            select(ProjectMembership)
            .options(selectinload(ProjectMembership.user))
            .where(ProjectMembership.project_id == project_id)
            .order_by(ProjectMembership.created_at.asc())
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_team_workload_task_aggregates(
        self,
        project_id: uuid.UUID,
        now_utc: datetime,
        completed_since: datetime | None = None,
    ) -> list[dict]:
        """
        Group tasks by assignee_id and calculate total, in_progress, overdue,
        completed, and story points in a single SQL aggregate query.
        """
        is_completed_cond = func.lower(WorkItem.status).in_(COMPLETED_STATUSES)
        is_in_progress_cond = and_(
            func.lower(WorkItem.status).in_(IN_PROGRESS_DEFAULT_STATUSES),
            ~is_completed_cond,
        )
        is_overdue_cond = and_(
            WorkItem.due_date.is_not(None),
            WorkItem.due_date < now_utc,
            ~is_completed_cond,
        )

        completed_filter: ColumnElement[bool] = is_completed_cond
        if completed_since is not None:
            completed_filter = and_(
                is_completed_cond,
                func.coalesce(WorkItem.completed_at, WorkItem.updated_at)
                >= completed_since,
            )

        stmt = (
            select(
                WorkItem.assignee_id,
                func.count(WorkItem.id).label("total_assigned"),
                func.count(case((~is_completed_cond, 1))).label("active_assigned"),
                func.count(case((is_in_progress_cond, 1))).label("in_progress"),
                func.count(case((is_overdue_cond, 1))).label("overdue"),
                func.count(case((completed_filter, 1))).label("completed"),
                func.coalesce(func.sum(WorkItem.estimated_points), 0.0).label(
                    "total_points"
                ),
                func.coalesce(
                    func.sum(case((is_completed_cond, WorkItem.estimated_points))), 0.0
                ).label("completed_points"),
            )
            .where(WorkItem.project_id == project_id)
            .group_by(WorkItem.assignee_id)
        )
        res = await self.session.execute(stmt)

        rows = []
        for row in res.all():
            rows.append(
                {
                    "assignee_id": row.assignee_id,
                    "total_assigned": row.total_assigned or 0,
                    "active_assigned": row.active_assigned or 0,
                    "in_progress": row.in_progress or 0,
                    "overdue": row.overdue or 0,
                    "completed": row.completed or 0,
                    "total_points": float(row.total_points or 0.0),
                    "completed_points": float(row.completed_points or 0.0),
                }
            )
        return rows
