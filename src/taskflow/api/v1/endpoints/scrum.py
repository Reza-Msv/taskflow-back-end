import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from taskflow.api.deps import (
    get_current_active_user,
    get_organization_service,
    get_project_service,
    get_scrum_service,
)
from taskflow.api.v1.endpoints.projects import (
    can_manage_project,
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
from taskflow.domains.scrum.schemas import (
    BacklogMoveTasksRequest,
    BacklogReorderRequest,
    BacklogSummaryResponse,
    EstimateTaskRequest,
    ProjectVelocityResponse,
    SprintAssignTasksRequest,
    SprintBurndownResponse,
    SprintCompleteRequest,
    SprintCreateRequest,
    SprintListResponse,
    SprintResponse,
    SprintRetrospectiveRequest,
    SprintRetrospectiveResponse,
    SprintStartRequest,
    SprintSummaryResponse,
    SprintUpdateRequest,
)
from taskflow.domains.scrum.service import ScrumService
from taskflow.domains.work_items.schemas import WorkItemListResponse, WorkItemResponse

router = APIRouter(
    prefix="/organizations/{organization_id}/projects/{project_id}",
    tags=["Scrum & Sprints"],
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


def _enforce_manage_project(
    project: Project,
    user_id: uuid.UUID,
    org_membership: OrganizationMembership,
    proj_membership: ProjectMembership | None,
) -> None:
    """Enforce project administration or owner permissions."""
    if not can_manage_project(project, user_id, org_membership, proj_membership):
        raise DomainException(
            code="FORBIDDEN",
            message=(
                "Project administrator or manager privileges required "
                "for this sprint operation."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
            details={"project_id": str(project.id)},
        )


def _enforce_can_modify_task(
    org_membership: OrganizationMembership,
    proj_membership: ProjectMembership | None,
) -> None:
    """Enforce that user is not a read-only viewer."""
    if org_membership.role in {"owner", "admin"}:
        return
    if proj_membership is not None and proj_membership.role == "viewer":
        raise DomainException(
            code="FORBIDDEN",
            message="Viewers are not permitted to modify task attributes.",
            status_code=status.HTTP_403_FORBIDDEN,
        )


# -------------------------------------------------------------
# Product Backlog Endpoints
# -------------------------------------------------------------


@router.get(
    "/backlog",
    response_model=BacklogSummaryResponse,
    summary="Retrieve Product Backlog tasks",
)
async def get_backlog(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    priority: Annotated[str | None, Query()] = None,
    assignee_id: Annotated[uuid.UUID | None, Query()] = None,
    type: Annotated[str | None, Query()] = None,
    search: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
    sort_by: Annotated[str, Query()] = "rank",
    sort_dir: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
) -> BacklogSummaryResponse:
    """List tasks in the Product Backlog (sprint_id is NULL) with aggregated metrics."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await scrum_service.get_backlog(
        org_id=organization_id,
        project_id=project_id,
        status_filter=status_filter,
        priority=priority,
        assignee_id=assignee_id,
        type=type,
        search=search,
        page=page,
        page_size=page_size,
        sort_by=sort_by,
        sort_dir=sort_dir,
    )


@router.post(
    "/backlog/tasks",
    response_model=list[WorkItemResponse],
    summary="Move tasks to the Product Backlog",
)
async def move_tasks_to_backlog(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: BacklogMoveTasksRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> list[WorkItemResponse]:
    """Unassign tasks from sprints and return them to the Product Backlog."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify_task(org_membership, proj_membership)

    return await scrum_service.move_tasks_to_backlog(
        org_id=organization_id,
        project_id=project_id,
        task_ids=payload.task_ids,
        actor_id=current_user.id,
    )


@router.put(
    "/backlog/reorder",
    response_model=list[WorkItemResponse],
    summary="Prioritize and reorder Product Backlog tasks",
)
async def reorder_backlog(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: BacklogReorderRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> list[WorkItemResponse]:
    """Reorder and reprioritize backlog tasks."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify_task(org_membership, proj_membership)

    return await scrum_service.reorder_backlog(
        org_id=organization_id,
        project_id=project_id,
        request=payload,
        actor_id=current_user.id,
    )


# -------------------------------------------------------------
# Sprint CRUD Endpoints
# -------------------------------------------------------------


@router.post(
    "/sprints",
    response_model=SprintResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new Sprint",
)
async def create_sprint(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    payload: SprintCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintResponse:
    """Create a new Sprint in draft status."""
    project, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_manage_project(project, current_user.id, org_membership, proj_membership)

    return await scrum_service.create_sprint(
        org_id=organization_id,
        project_id=project_id,
        request=payload,
    )


@router.get(
    "/sprints",
    response_model=SprintListResponse,
    summary="List project Sprints",
)
async def list_sprints(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
) -> SprintListResponse:
    """List Sprints for a project with optional status filter."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    sprints = await scrum_service.list_sprints(
        org_id=organization_id,
        project_id=project_id,
        status_filter=status_filter,
    )
    return SprintListResponse(items=sprints, total=len(sprints))


@router.get(
    "/sprints/velocity",
    response_model=ProjectVelocityResponse,
    summary="Get project velocity across completed Sprints",
)
async def get_project_velocity(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> ProjectVelocityResponse:
    """Calculate average and per-sprint velocity across historical completed Sprints."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await scrum_service.get_project_velocity(
        org_id=organization_id,
        project_id=project_id,
    )


@router.get(
    "/sprints/{sprint_id}",
    response_model=SprintResponse,
    summary="Retrieve Sprint details",
)
async def get_sprint(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintResponse:
    """Get Sprint by ID."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await scrum_service.get_sprint(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
    )


@router.patch(
    "/sprints/{sprint_id}",
    response_model=SprintResponse,
    summary="Update Sprint details",
)
async def update_sprint(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    payload: SprintUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintResponse:
    """Update mutable details of a Sprint."""
    project, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_manage_project(project, current_user.id, org_membership, proj_membership)

    return await scrum_service.update_sprint(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
        request=payload,
    )


@router.delete(
    "/sprints/{sprint_id}",
    response_model=MessageResponse,
    summary="Delete a Sprint",
)
async def delete_sprint(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> MessageResponse:
    """Delete a Sprint. Returns all associated tasks safely to the Product Backlog."""
    project, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_manage_project(project, current_user.id, org_membership, proj_membership)

    await scrum_service.delete_sprint(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
    )
    return MessageResponse(
        message="Sprint deleted successfully. All assigned tasks returned to backlog."
    )


# -------------------------------------------------------------
# Sprint Lifecycle Endpoints
# -------------------------------------------------------------


@router.post(
    "/sprints/{sprint_id}/start",
    response_model=SprintResponse,
    summary="Start a Sprint",
)
async def start_sprint(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    payload: SprintStartRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintResponse:
    """
    Start a Sprint (draft -> active).
    Enforces single active sprint policy and records starting snapshot.
    """
    project, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_manage_project(project, current_user.id, org_membership, proj_membership)

    return await scrum_service.start_sprint(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
        request=payload,
    )


@router.post(
    "/sprints/{sprint_id}/complete",
    response_model=SprintResponse,
    summary="Complete an active Sprint",
)
async def complete_sprint(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    payload: SprintCompleteRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintResponse:
    """
    Complete an active Sprint (active -> completed).
    Rollovers unfinished tasks to backlog or target sprint without silent data loss.
    """
    project, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_manage_project(project, current_user.id, org_membership, proj_membership)

    return await scrum_service.complete_sprint(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
        request=payload,
        actor_id=current_user.id,
    )


# -------------------------------------------------------------
# Sprint Task Assignment Endpoints
# -------------------------------------------------------------


@router.get(
    "/sprints/{sprint_id}/tasks",
    response_model=WorkItemListResponse,
    summary="List tasks assigned to a Sprint",
)
async def list_sprint_tasks(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 100,
) -> WorkItemListResponse:
    """Get all tasks assigned to a specific Sprint."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    skip = (page - 1) * page_size
    items, total = await scrum_service.list_sprint_tasks(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
        skip=skip,
        limit=page_size,
    )
    return WorkItemListResponse(
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/sprints/{sprint_id}/tasks",
    response_model=list[WorkItemResponse],
    summary="Assign tasks to a Sprint",
)
async def assign_tasks_to_sprint(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    payload: SprintAssignTasksRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> list[WorkItemResponse]:
    """Assign existing project tasks into a designated Sprint."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify_task(org_membership, proj_membership)

    return await scrum_service.assign_tasks_to_sprint(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
        task_ids=payload.task_ids,
        actor_id=current_user.id,
    )


@router.delete(
    "/sprints/{sprint_id}/tasks/{task_id}",
    response_model=WorkItemResponse,
    summary="Remove a task from a Sprint",
)
async def remove_task_from_sprint(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> WorkItemResponse:
    """Remove a task from a Sprint, returning it directly to the Product Backlog."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify_task(org_membership, proj_membership)

    return await scrum_service.remove_task_from_sprint(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
        task_id=task_id,
        actor_id=current_user.id,
    )


# -------------------------------------------------------------
# Task Story Point Estimation Endpoint
# -------------------------------------------------------------


@router.post(
    "/tasks/{task_id}/estimate",
    response_model=WorkItemResponse,
    summary="Estimate story points for a task",
)
async def estimate_task(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    payload: EstimateTaskRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> WorkItemResponse:
    """Set or update story point estimate for a task with audit logging."""
    _, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_can_modify_task(org_membership, proj_membership)

    return await scrum_service.estimate_task(
        org_id=organization_id,
        project_id=project_id,
        task_id=task_id,
        request=payload,
        actor_id=current_user.id,
    )


# -------------------------------------------------------------
# Sprint Metrics & Reports Endpoints
# -------------------------------------------------------------


@router.get(
    "/sprints/{sprint_id}/summary",
    response_model=SprintSummaryResponse,
    summary="Get Sprint Summary metrics",
)
async def get_sprint_summary(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintSummaryResponse:
    """Get aggregated completion status, task counts, and story points."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await scrum_service.get_sprint_summary(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
    )


@router.get(
    "/sprints/{sprint_id}/burndown",
    response_model=SprintBurndownResponse,
    summary="Get Sprint Burndown time series data",
)
async def get_sprint_burndown(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintBurndownResponse:
    """Get daily burndown progression comparing actual work against ideal velocity."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await scrum_service.get_sprint_burndown(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
    )


# -------------------------------------------------------------
# Sprint Retrospective Endpoints
# -------------------------------------------------------------


@router.get(
    "/sprints/{sprint_id}/retrospective",
    response_model=SprintRetrospectiveResponse | None,
    summary="Get Sprint Retrospective",
)
async def get_sprint_retrospective(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintRetrospectiveResponse | None:
    """Retrieve retrospective review for a sprint."""
    await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    return await scrum_service.get_retrospective(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
    )


@router.put(
    "/sprints/{sprint_id}/retrospective",
    response_model=SprintRetrospectiveResponse,
    summary="Create or update Sprint Retrospective",
)
async def save_sprint_retrospective(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    sprint_id: uuid.UUID,
    payload: SprintRetrospectiveRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    scrum_service: Annotated[ScrumService, Depends(get_scrum_service)],
) -> SprintRetrospectiveResponse:
    """Save or update retrospective review items for a sprint."""
    project, org_membership, proj_membership = await _authorize_project_view(
        organization_id, project_id, current_user, org_service, project_service
    )
    _enforce_manage_project(project, current_user.id, org_membership, proj_membership)

    return await scrum_service.save_retrospective(
        org_id=organization_id,
        project_id=project_id,
        sprint_id=sprint_id,
        request=payload,
    )
