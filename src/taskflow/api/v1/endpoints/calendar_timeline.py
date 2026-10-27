import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from taskflow.api.deps import (
    get_current_active_user,
    get_organization_service,
    get_project_service,
    get_task_service,
)
from taskflow.api.v1.endpoints.projects import (
    get_org_membership,
    verify_project_access,
)
from taskflow.core.exceptions import DomainException
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import MessageResponse
from taskflow.domains.organizations.models import OrganizationMembership
from taskflow.domains.organizations.service import OrganizationService
from taskflow.domains.projects.models import Project, ProjectMembership
from taskflow.domains.projects.service import ProjectService
from taskflow.domains.work_items.schemas import (
    CalendarResponse,
    MilestoneCreateRequest,
    MilestoneListResponse,
    MilestoneResponse,
    MilestoneUpdateRequest,
    TaskDependenciesSummary,
    TaskDependencyCreateRequest,
    TaskDependencyListResponse,
    TaskDependencyResponse,
    TimelineResponse,
)
from taskflow.domains.work_items.service import TaskService

router = APIRouter(
    prefix="/organizations/{organization_id}/projects/{project_id}",
    tags=["Calendar, Timeline & Milestones"],
)


async def _authorize_project_view(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: User,
    org_service: OrganizationService,
    project_service: ProjectService,
) -> tuple[Project, OrganizationMembership, ProjectMembership | None]:
    """Verify active org membership and project view authorization."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )
    return project, org_membership, proj_membership


def _enforce_can_modify(
    org_membership: OrganizationMembership,
    proj_membership: ProjectMembership | None,
) -> None:
    """Enforce that user is not a read-only viewer."""
    if org_membership.role in {"owner", "admin"}:
        return
    if proj_membership is not None and proj_membership.role == "viewer":
        raise DomainException(
            code="FORBIDDEN",
            message="Viewers are not permitted to modify project resources.",
            status_code=status.HTTP_403_FORBIDDEN,
        )
    if org_membership.role in {"viewer", "guest"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Guest/viewer role cannot modify project resources.",
            status_code=status.HTTP_403_FORBIDDEN,
        )


# -------------------------------------------------------------
# Calendar View API
# -------------------------------------------------------------


@router.get(
    "/calendar",
    response_model=CalendarResponse,
    summary="Get calendar tasks across date range",
)
async def get_calendar_view(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    start_date: Annotated[
        datetime,
        Query(description="Calendar range start timestamp (UTC/ISO-8601)"),
    ],
    end_date: Annotated[
        datetime,
        Query(description="Calendar range end timestamp (UTC/ISO-8601)"),
    ],
    assignee_id: Annotated[
        uuid.UUID | None,
        Query(description="Filter tasks by assignee user ID"),
    ] = None,
    task_status: Annotated[
        str | None,
        Query(alias="status", description="Filter tasks by status name"),
    ] = None,
    include_unscheduled: Annotated[
        bool,
        Query(
            description="Whether to include tasks without start or due date"
        ),
    ] = False,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> CalendarResponse:
    """Return tasks spanning or falling within the specified calendar date window."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await task_service.get_calendar_view(
        org_id=organization_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        assignee_id=assignee_id,
        task_status=task_status,
        include_unscheduled=include_unscheduled,
    )


# -------------------------------------------------------------
# Timeline / Gantt View API
# -------------------------------------------------------------


@router.get(
    "/timeline",
    response_model=TimelineResponse,
    summary="Get timeline / Gantt chart data",
)
async def get_timeline_view(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    start_date: Annotated[
        datetime | None,
        Query(description="Optional timeline window start (UTC/ISO-8601)"),
    ] = None,
    end_date: Annotated[
        datetime | None,
        Query(description="Optional timeline window end (UTC/ISO-8601)"),
    ] = None,
    include_unscheduled: Annotated[
        bool,
        Query(description="Include tasks without dates"),
    ] = True,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> TimelineResponse:
    """Return tasks, milestones, and dependencies for Timeline/Gantt rendering."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await task_service.get_timeline_view(
        org_id=organization_id,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        include_unscheduled=include_unscheduled,
    )


# -------------------------------------------------------------
# Project Milestones API
# -------------------------------------------------------------


@router.post(
    "/milestones",
    response_model=MilestoneResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a project milestone",
)
async def create_milestone(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: MilestoneCreateRequest,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> MilestoneResponse:
    """Create a new delivery milestone in the project."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify(org_membership, proj_membership)
    return await task_service.create_milestone(
        org_id=organization_id,
        project_id=project_id,
        actor_id=current_user.id,
        request=request,
    )


@router.get(
    "/milestones",
    response_model=MilestoneListResponse,
    summary="List project milestones",
)
async def list_milestones(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    is_completed: Annotated[
        bool | None,
        Query(description="Filter by completion status"),
    ] = None,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> MilestoneListResponse:
    """Retrieve all milestones configured for the project."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await task_service.list_milestones(
        org_id=organization_id,
        project_id=project_id,
        is_completed=is_completed,
    )


@router.get(
    "/milestones/{milestone_id}",
    response_model=MilestoneResponse,
    summary="Get milestone details",
)
async def get_milestone(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    milestone_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> MilestoneResponse:
    """Retrieve specific milestone details and task completion metrics."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await task_service.get_milestone(
        org_id=organization_id,
        project_id=project_id,
        milestone_id=milestone_id,
    )


@router.patch(
    "/milestones/{milestone_id}",
    response_model=MilestoneResponse,
    summary="Update milestone",
)
async def update_milestone(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    milestone_id: uuid.UUID,
    request: MilestoneUpdateRequest,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> MilestoneResponse:
    """Update milestone details or completion flag."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify(org_membership, proj_membership)
    return await task_service.update_milestone(
        org_id=organization_id,
        project_id=project_id,
        milestone_id=milestone_id,
        actor_id=current_user.id,
        request=request,
    )


@router.delete(
    "/milestones/{milestone_id}",
    response_model=MessageResponse,
    summary="Delete milestone",
)
async def delete_milestone(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    milestone_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> MessageResponse:
    """Delete a milestone and unassign attached tasks."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify(org_membership, proj_membership)
    await task_service.delete_milestone(
        org_id=organization_id,
        project_id=project_id,
        milestone_id=milestone_id,
        actor_id=current_user.id,
    )
    return MessageResponse(message="Milestone deleted successfully.")


# -------------------------------------------------------------
# Task Dependencies API
# -------------------------------------------------------------


@router.post(
    "/dependencies",
    response_model=TaskDependencyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task dependency",
)
async def create_dependency(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: TaskDependencyCreateRequest,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> TaskDependencyResponse:
    """Establish a finish-to-start dependency between two tasks in the project."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify(org_membership, proj_membership)
    return await task_service.create_dependency(
        org_id=organization_id,
        project_id=project_id,
        actor_id=current_user.id,
        request=request,
    )


@router.get(
    "/dependencies",
    response_model=TaskDependencyListResponse,
    summary="List project task dependencies",
)
async def list_dependencies(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> TaskDependencyListResponse:
    """Retrieve all dependency relationships between tasks in the project."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await task_service.list_dependencies_for_project(
        org_id=organization_id,
        project_id=project_id,
    )


@router.get(
    "/tasks/{task_id}/dependencies",
    response_model=TaskDependenciesSummary,
    summary="List direct dependencies for a task",
)
async def list_task_dependencies(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> TaskDependenciesSummary:
    """Retrieve predecessor and successor dependencies connected to a specific task."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await task_service.list_dependencies_for_task(
        org_id=organization_id,
        project_id=project_id,
        task_id=task_id,
    )


@router.delete(
    "/dependencies/{dependency_id}",
    response_model=MessageResponse,
    summary="Delete a task dependency",
)
async def delete_dependency(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    dependency_id: uuid.UUID,
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> MessageResponse:
    """Remove a task dependency relationship."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify(org_membership, proj_membership)
    await task_service.delete_dependency(
        org_id=organization_id,
        project_id=project_id,
        dependency_id=dependency_id,
        actor_id=current_user.id,
    )
    return MessageResponse(message="Task dependency deleted successfully.")
