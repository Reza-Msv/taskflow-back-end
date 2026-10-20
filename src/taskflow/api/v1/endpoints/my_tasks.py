import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from taskflow.api.deps import get_current_active_user, get_task_service
from taskflow.domains.identity.models import User
from taskflow.domains.work_items.schemas import (
    WorkItemListResponse,
    WorkItemResponse,
)
from taskflow.domains.work_items.service import TaskService

router = APIRouter(prefix="/tasks", tags=["My Tasks"])


@router.get(
    "/me",
    response_model=WorkItemListResponse,
    status_code=status.HTTP_200_OK,
    summary="Get My Tasks",
    description=(
        "Retrieve tasks assigned to the authenticated user across accessible "
        "projects, supporting advanced status, priority, due date, overdue, "
        "completion, and search filters."
    ),
)
async def get_my_tasks(
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    page: Annotated[int, Query(ge=1, description="Page number")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=100, description="Items per page")
    ] = 25,
    organization_id: Annotated[
        uuid.UUID | None, Query(description="Filter by organization ID")
    ] = None,
    project_id: Annotated[
        uuid.UUID | None, Query(description="Filter by project ID")
    ] = None,
    status: Annotated[
        str | None, Query(description="Filter by status name")
    ] = None,
    priority: Annotated[
        str | None, Query(description="Filter by priority")
    ] = None,
    type: Annotated[
        str | None, Query(description="Filter by work item type")
    ] = None,
    search: Annotated[
        str | None, Query(description="Search title, key, or description")
    ] = None,
    due_date_from: Annotated[
        datetime | None, Query(description="Due date start range")
    ] = None,
    due_date_to: Annotated[
        datetime | None, Query(description="Due date end range")
    ] = None,
    is_overdue: Annotated[
        bool | None, Query(description="Filter overdue tasks")
    ] = None,
    due_soon: Annotated[
        bool | None, Query(description="Filter tasks due in next 7 days")
    ] = None,
    is_completed: Annotated[
        bool | None, Query(description="Filter completed tasks")
    ] = None,
    label_id: Annotated[
        uuid.UUID | None, Query(description="Filter by taxonomic label ID")
    ] = None,
    sort_by: Annotated[
        str,
        Query(
            description=(
                "Sort field: smart_order, due_date, priority, "
                "created_at, updated_at, title"
            )
        ),
    ] = "smart_order",
    sort_dir: Annotated[
        str, Query(description="Sort direction: asc, desc")
    ] = "asc",
) -> WorkItemListResponse:
    """Retrieve personal tasks assigned to current user."""
    tasks, total = await task_service.get_my_tasks(
        user_id=current_user.id,
        organization_id=organization_id,
        project_id=project_id,
        status=status,
        priority=priority,
        type=type,
        search=search,
        due_date_from=due_date_from,
        due_date_to=due_date_to,
        is_overdue=is_overdue,
        due_soon=due_soon,
        is_completed=is_completed,
        label_id=label_id,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        page_size=page_size,
    )
    return WorkItemListResponse(
        items=[WorkItemResponse.model_validate(t) for t in tasks],
        page=page,
        page_size=page_size,
        total=total,
    )
