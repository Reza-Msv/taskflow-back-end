import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from taskflow.api.deps import get_current_active_user, get_dashboard_service
from taskflow.domains.identity.models import User
from taskflow.domains.work_items.schemas import (
    DashboardSummaryResponse,
    OverdueUpcomingTasksResponse,
    UserActivityListResponse,
    WorkItemListDashboardResponse,
)
from taskflow.domains.work_items.service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["User Dashboard"])


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    status_code=status.HTTP_200_OK,
    summary="User Dashboard Summary",
    description=(
        "Returns project summaries and aggregate task counts (assigned, in-progress, "
        "completed, overdue) for the authenticated user."
    ),
)
async def get_dashboard_summary(
    current_user: Annotated[User, Depends(get_current_active_user)],
    dashboard_service: Annotated[DashboardService, Depends(get_dashboard_service)],
) -> DashboardSummaryResponse:
    """Retrieve summary metrics for the current authenticated user's dashboard."""
    return await dashboard_service.get_dashboard_summary(current_user.id)


@router.get(
    "/tasks",
    response_model=WorkItemListDashboardResponse,
    status_code=status.HTTP_200_OK,
    summary="My Assigned Tasks",
    description=(
        "Returns a paginated list of tasks assigned to the authenticated user with "
        "filtering by status, project, due date, and smart/custom ordering."
    ),
)
async def get_my_tasks(
    current_user: Annotated[User, Depends(get_current_active_user)],
    dashboard_service: Annotated[DashboardService, Depends(get_dashboard_service)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 25,
    status: Annotated[
        str | None,
        Query(description="Filter by task status (e.g. todo, in_progress, done)"),
    ] = None,
    project_id: Annotated[
        uuid.UUID | None, Query(description="Filter by project ID")
    ] = None,
    due_date_from: Annotated[
        datetime | None,
        Query(description="Filter tasks with due date on or after this UTC timestamp"),
    ] = None,
    due_date_to: Annotated[
        datetime | None,
        Query(description="Filter tasks with due date on or before this UTC timestamp"),
    ] = None,
    sort_by: Annotated[
        Literal["smart_order", "due_date", "priority", "created_at", "updated_at"],
        Query(
            description=(
                "Ordering mode: 'smart_order' prioritizes overdue and "
                "upcoming tasks first; 'due_date', 'priority', "
                "'created_at', or 'updated_at'."
            )
        ),
    ] = "smart_order",
    sort_dir: Annotated[
        Literal["asc", "desc"], Query(description="Sort direction ('asc' or 'desc')")
    ] = "asc",
) -> WorkItemListDashboardResponse:
    """Retrieve filtered, paginated tasks assigned to the current user."""
    return await dashboard_service.get_my_tasks(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
        status=status,
        project_id=project_id,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@router.get(
    "/tasks/overdue-upcoming",
    response_model=OverdueUpcomingTasksResponse,
    status_code=status.HTTP_200_OK,
    summary="Overdue and Upcoming Tasks",
    description=(
        "Returns active tasks assigned to the user that are overdue or approaching "
        "their due dates within the upcoming window."
    ),
)
async def get_overdue_upcoming_tasks(
    current_user: Annotated[User, Depends(get_current_active_user)],
    dashboard_service: Annotated[DashboardService, Depends(get_dashboard_service)],
    upcoming_days: Annotated[
        int,
        Query(
            ge=1,
            le=60,
            description=(
                "Number of days ahead to look for upcoming tasks approaching due date"
            ),
        ),
    ] = 7,
    limit: Annotated[
        int,
        Query(ge=1, le=100, description="Maximum number of items per section"),
    ] = 20,
) -> OverdueUpcomingTasksResponse:
    """Retrieve user's overdue tasks and upcoming tasks approaching due dates."""
    return await dashboard_service.get_overdue_and_upcoming_tasks(
        user_id=current_user.id,
        upcoming_days=upcoming_days,
        limit_per_group=limit,
    )


@router.get(
    "/activity",
    response_model=UserActivityListResponse,
    status_code=status.HTTP_200_OK,
    summary="Recent User Activity",
    description=(
        "Returns genuine auditable activity stream events for tasks assigned "
        "to the user or actions performed by the user."
    ),
)
async def get_recent_activity(

    current_user: Annotated[User, Depends(get_current_active_user)],
    dashboard_service: Annotated[DashboardService, Depends(get_dashboard_service)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 25,
) -> UserActivityListResponse:
    """Retrieve real activity stream events relevant to the authenticated user."""
    return await dashboard_service.get_recent_activity(
        user_id=current_user.id,
        page=page,
        page_size=page_size,
    )
