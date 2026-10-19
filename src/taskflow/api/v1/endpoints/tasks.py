import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from taskflow.api.deps import (
    get_current_active_user,
    get_organization_service,
    get_project_service,
    get_task_service,
)
from taskflow.api.v1.endpoints.projects import (
    can_manage_project,
    get_org_membership,
    verify_project_access,
)
from taskflow.core.exceptions import DomainException
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import MessageResponse
from taskflow.domains.organizations.service import OrganizationService
from taskflow.domains.projects.service import ProjectService
from taskflow.domains.work_items.schemas import (
    KanbanBoardResponse,
    TaskStatusMoveRequest,
    WorkItemCreateRequest,
    WorkItemHistoryListResponse,
    WorkItemListResponse,
    WorkItemResponse,
    WorkItemUpdateRequest,
)
from taskflow.domains.work_items.service import TaskService

router = APIRouter(
    prefix="/organizations/{organization_id}/projects/{project_id}",
    tags=["Tasks, Kanban & History"],
)


def can_modify_task(
    project_lead_id: uuid.UUID | None,
    org_role: str,
    proj_role: str | None,
    task_reporter_id: uuid.UUID | None,
    task_assignee_id: uuid.UUID | None,
    current_user_id: uuid.UUID,
) -> bool:
    """
    Check if user is authorized to modify a task:
    - Org owner or admin
    - Project lead (owner) or project admin
    - Task reporter or assignee
    - Regular project member
    Viewers cannot modify tasks.
    """
    if org_role in {"owner", "admin"}:
        return True
    if project_lead_id == current_user_id:
        return True
    if proj_role in {"admin", "member"}:
        return True
    if task_reporter_id == current_user_id or task_assignee_id == current_user_id:
        return True
    if proj_role == "viewer" or org_role in {"viewer", "guest"}:
        return False
    return True


# -------------------------------------------------------------
# Task CRUD Endpoints
# -------------------------------------------------------------


@router.post(
    "/tasks",
    response_model=WorkItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Task in Project",
)
async def create_task(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: WorkItemCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> WorkItemResponse:
    """
    Create a new task within a project.
    Validates project access, assignee eligibility, and records creation activity.
    """
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    proj_role = proj_membership.role if proj_membership else None
    if proj_role == "viewer" or org_membership.role in {"viewer", "guest"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Viewers do not have permission to create tasks in this project.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    task = await task_service.create_task(
        project_id=project_id,
        org_id=organization_id,
        creator_id=current_user.id,
        request=request,
    )
    return WorkItemResponse.model_validate(task)


@router.get(
    "/tasks",
    response_model=WorkItemListResponse,
    summary="List Tasks in Project",
)
async def list_tasks(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
    status: str | None = Query(None, description="Filter by status name"),
    assignee_id: uuid.UUID | None = Query(None, description="Filter by assignee user ID"),
    priority: str | None = Query(None, description="Filter by priority"),
    search: str | None = Query(None, description="Search by title or issue key"),
) -> WorkItemListResponse:
    """List tasks in a project with pagination and filters."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    await verify_project_access(project, current_user.id, org_membership, project_service)

    tasks, total = await task_service.list_tasks(
        project_id=project_id,
        status=status,
        assignee_id=assignee_id,
        priority=priority,
        search=search,
        page=page,
        page_size=page_size,
    )
    return WorkItemListResponse(
        items=[WorkItemResponse.model_validate(t) for t in tasks],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/tasks/{task_id}",
    response_model=WorkItemResponse,
    summary="Get Task Details",
)
async def get_task_details(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> WorkItemResponse:
    """Retrieve full task details with authorization check."""
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    await verify_project_access(project, current_user.id, org_membership, project_service)

    task = await task_service.get_task_by_id(task_id, project_id=project_id)
    return WorkItemResponse.model_validate(task)


@router.patch(
    "/tasks/{task_id}",
    response_model=WorkItemResponse,
    summary="Update Task",
)
async def update_task(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    request: WorkItemUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> WorkItemResponse:
    """
    Update task fields (title, description, priority, assignee, due date, estimated points).
    Enforces role permissions, assignee eligibility, and optimistic concurrency.
    """
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    task = await task_service.get_task_by_id(task_id, project_id=project_id)
    proj_role = proj_membership.role if proj_membership else None

    if not can_modify_task(
        project_lead_id=project.owner_id,
        org_role=org_membership.role,
        proj_role=proj_role,
        task_reporter_id=task.reporter_id,
        task_assignee_id=task.assignee_id,
        current_user_id=current_user.id,
    ):
        raise DomainException(
            code="FORBIDDEN",
            message="You do not have permission to modify this task.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    updated_task = await task_service.update_task(
        task_id=task_id,
        project_id=project_id,
        actor_id=current_user.id,
        request=request,
    )
    return WorkItemResponse.model_validate(updated_task)


@router.delete(
    "/tasks/{task_id}",
    response_model=MessageResponse,
    summary="Delete Task",
)
async def delete_task(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> MessageResponse:
    """
    Delete task.
    Allowed for: org admin/owner, project lead/admin, or original task reporter.
    """
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    task = await task_service.get_task_by_id(task_id, project_id=project_id)

    is_allowed = (
        can_manage_project(project, current_user.id, org_membership, proj_membership)
        or task.reporter_id == current_user.id
    )
    if not is_allowed:
        raise DomainException(
            code="FORBIDDEN",
            message="You do not have permission to delete this task.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await task_service.delete_task(
        task_id=task_id,
        project_id=project_id,
        actor_id=current_user.id,
    )
    return MessageResponse(message="Task deleted successfully.")


# -------------------------------------------------------------
# Kanban Board & Status Transitions
# -------------------------------------------------------------


@router.get(
    "/kanban",
    response_model=KanbanBoardResponse,
    summary="Get Kanban Board",
)
async def get_kanban_board(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> KanbanBoardResponse:
    """
    Retrieve project tasks grouped by workflow status columns in position order.
    """
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    await verify_project_access(project, current_user.id, org_membership, project_service)

    return await task_service.get_kanban_board(project_id)


@router.post(
    "/tasks/{task_id}/move",
    response_model=WorkItemResponse,
    summary="Move Task / Update Kanban Status and Order",
)
async def move_task_status(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    request: TaskStatusMoveRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> WorkItemResponse:
    """
    Move task to a new status column or update ordering rank.
    Validates workflow transitions, optimistic concurrency version, and records history.
    """
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    task = await task_service.get_task_by_id(task_id, project_id=project_id)
    proj_role = proj_membership.role if proj_membership else None

    if not can_modify_task(
        project_lead_id=project.owner_id,
        org_role=org_membership.role,
        proj_role=proj_role,
        task_reporter_id=task.reporter_id,
        task_assignee_id=task.assignee_id,
        current_user_id=current_user.id,
    ):
        raise DomainException(
            code="FORBIDDEN",
            message="You do not have permission to move or update this task.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    updated_task = await task_service.move_task_status(
        task_id=task_id,
        project_id=project_id,
        actor_id=current_user.id,
        request=request,
    )
    return WorkItemResponse.model_validate(updated_task)


# -------------------------------------------------------------
# Task History / Audit Activity Stream
# -------------------------------------------------------------


@router.get(
    "/tasks/{task_id}/history",
    response_model=WorkItemHistoryListResponse,
    summary="Get Task History",
)
async def get_task_history(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
) -> WorkItemHistoryListResponse:
    """
    Retrieve paginated, append-only history and activity events for a work item.
    """
    org_membership = await get_org_membership(organization_id, current_user.id, org_service)
    project = await project_service.get_project_by_id(project_id, org_id=organization_id)
    await verify_project_access(project, current_user.id, org_membership, project_service)

    activities, total = await task_service.get_task_history(
        task_id=task_id,
        project_id=project_id,
        page=page,
        page_size=page_size,
    )
    return WorkItemHistoryListResponse(
        items=activities,
        page=page,
        page_size=page_size,
        total=total,
    )
