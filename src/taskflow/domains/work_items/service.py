import math
import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.domains.work_items.models import WorkItem
from taskflow.domains.work_items.repository import (
    COMPLETED_STATUSES,
    DashboardRepository,
)
from taskflow.domains.work_items.schemas import (
    DashboardSummaryResponse,
    OverdueUpcomingTasksResponse,
    ProjectBrief,
    ProjectSummary,
    UserActivityActor,
    UserActivityItem,
    UserActivityListResponse,
    WorkItemDashboardItem,
    WorkItemListDashboardResponse,
)


class DashboardService:
    """
    Business service orchestrating user dashboard metrics, task lists, and activity.
    """


    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DashboardRepository(session)

    @staticmethod
    def _to_dashboard_item(item: WorkItem, now: datetime) -> WorkItemDashboardItem:
        """Helper to map WorkItem model to WorkItemDashboardItem schema."""
        is_completed = item.status.lower() in COMPLETED_STATUSES
        due_date = item.due_date
        if due_date is not None and due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=UTC)
        is_overdue = bool(due_date and due_date < now and not is_completed)

        project_brief = ProjectBrief(
            id=item.project.id,
            name=item.project.name,
            key=item.project.key,
            organization_id=item.project.organization_id,
            is_private=item.project.is_private,
        )

        return WorkItemDashboardItem(
            id=item.id,
            issue_key=item.issue_key,
            type=item.type,
            title=item.title,
            status=item.status,
            priority=item.priority,
            due_date=item.due_date,
            estimated_points=item.estimated_points,
            rank=item.rank,
            created_at=item.created_at,
            updated_at=item.updated_at,
            project=project_brief,
            is_overdue=is_overdue,
        )

    async def get_dashboard_summary(
        self, user_id: uuid.UUID
    ) -> DashboardSummaryResponse:
        """
        Produce aggregated dashboard summary for authenticated user:
        - Accessible projects with role and counts
        - Task counts grouped by status
        - Assigned, in-progress, completed, and overdue metrics
        """
        now = datetime.now(UTC)

        # Projects summary
        project_rows = await self.repo.get_accessible_projects(user_id)
        project_summaries = [
            ProjectSummary(
                id=proj.id,
                name=proj.name,
                key=proj.key,
                organization_id=proj.organization_id,
                is_private=proj.is_private,
                role=role,
                total_tasks=total_tasks,
                assigned_tasks=assigned_tasks,
            )
            for proj, role, total_tasks, assigned_tasks in project_rows
        ]

        # Task metrics
        metrics = await self.repo.get_user_task_metrics(user_id, now)

        return DashboardSummaryResponse(
            total_projects=len(project_summaries),
            projects=project_summaries,
            status_counts=metrics["status_counts"],
            assigned_tasks_count=metrics["total_assigned"],
            in_progress_tasks_count=metrics["in_progress_count"],
            completed_tasks_count=metrics["completed_count"],
            overdue_tasks_count=metrics["overdue_count"],
        )

    async def get_my_tasks(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
        project_id: uuid.UUID | None = None,
        due_date_from: datetime | None = None,
        due_date_to: datetime | None = None,
        sort_by: str = "smart_order",
        sort_dir: str = "asc",
    ) -> WorkItemListDashboardResponse:
        """
        Get tasks assigned to user with filtering, pagination, and smart ordering.
        """
        now = datetime.now(UTC)
        skip = (page - 1) * page_size
        items, total = await self.repo.list_assigned_tasks(
            user_id=user_id,
            now=now,
            skip=skip,
            limit=page_size,
            status=status,
            project_id=project_id,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        dtos = [self._to_dashboard_item(item, now) for item in items]
        total_pages = math.ceil(total / page_size) if page_size > 0 else 0

        return WorkItemListDashboardResponse(
            items=dtos,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    async def get_overdue_and_upcoming_tasks(
        self,
        user_id: uuid.UUID,
        upcoming_days: int = 7,
        limit_per_group: int = 20,
    ) -> OverdueUpcomingTasksResponse:
        """
        Retrieve overdue tasks and tasks approaching due date within `upcoming_days`.
        """
        now = datetime.now(UTC)
        overdue_items, upcoming_items = await self.repo.get_overdue_and_upcoming_tasks(
            user_id=user_id,
            now=now,
            upcoming_days=upcoming_days,
            limit_per_group=limit_per_group,
        )

        overdue_dtos = [self._to_dashboard_item(item, now) for item in overdue_items]
        upcoming_dtos = [self._to_dashboard_item(item, now) for item in upcoming_items]

        return OverdueUpcomingTasksResponse(
            overdue_tasks=overdue_dtos,
            upcoming_tasks=upcoming_dtos,
        )

    async def get_recent_activity(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> UserActivityListResponse:
        """
        Return real recorded activity stream items for user.
        """
        skip = (page - 1) * page_size
        activities, total = await self.repo.list_recent_activity(
            user_id=user_id,
            skip=skip,
            limit=page_size,
        )

        items = []
        for act in activities:
            actor_data = act.get("actor")
            actor = (
                UserActivityActor(
                    id=actor_data["id"],
                    email=actor_data["email"],
                    full_name=actor_data["full_name"],
                )
                if actor_data
                else None
            )

            items.append(
                UserActivityItem(
                    id=act["id"],
                    activity_type=act["activity_type"],
                    field=act["field"],
                    old_value=act["old_value"],
                    new_value=act["new_value"],
                    created_at=act["created_at"],
                    work_item_id=act["work_item_id"],
                    work_item_key=act["work_item_key"],
                    work_item_title=act["work_item_title"],
                    project_id=act["project_id"],
                    project_name=act["project_name"],
                    actor=actor,
                    details=act["details"],
                )
            )

        total_pages = math.ceil(total / page_size) if page_size > 0 else 0

        return UserActivityListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
