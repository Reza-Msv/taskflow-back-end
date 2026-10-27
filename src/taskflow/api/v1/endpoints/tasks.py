import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from fastapi.responses import FileResponse

from taskflow.api.deps import (
    get_collaboration_service,
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
from taskflow.domains.collaboration.schemas import (
    CommentCreateRequest,
    CommentUpdateRequest,
    MentionableUserResponse,
    WorkItemCommentListResponse,
    WorkItemCommentResponse,
)
from taskflow.domains.collaboration.service import CollaborationService
from taskflow.domains.identity.models import User
from taskflow.domains.identity.schemas import MessageResponse
from taskflow.domains.organizations.service import OrganizationService
from taskflow.domains.projects.service import ProjectService
from taskflow.domains.work_items.schemas import (
    AttachmentListResponse,
    AttachmentResponse,
    BulkTaskOperationRequest,
    BulkTaskOperationResponse,
    ChecklistItemCreateRequest,
    ChecklistItemResponse,
    ChecklistItemUpdateRequest,
    ChecklistListResponse,
    KanbanBoardResponse,
    SubtaskCreateRequest,
    TaskStatusMoveRequest,
    WorkItemCreateRequest,
    WorkItemDetailResponse,
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
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
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
    assignee_id: uuid.UUID | None = Query(
        None, description="Filter by assignee user ID"
    ),
    assigned_to_me: bool | None = Query(
        None, description="Filter tasks assigned to current user"
    ),
    reporter_id: uuid.UUID | None = Query(
        None, description="Filter by reporter/creator user ID"
    ),
    priority: str | None = Query(None, description="Filter by priority"),
    type: str | None = Query(None, description="Filter by work item type"),
    search: str | None = Query(None, description="Search title, key, or description"),
    due_date_from: datetime | None = Query(None, description="Due date starting range"),
    due_date_to: datetime | None = Query(None, description="Due date ending range"),
    is_overdue: bool | None = Query(
        None, description="Filter overdue incomplete tasks"
    ),
    due_soon: bool | None = Query(None, description="Filter tasks due in next 7 days"),
    is_completed: bool | None = Query(None, description="Filter completed tasks"),
    label_id: uuid.UUID | None = Query(
        None, description="Filter by taxonomic label ID"
    ),
    sort_by: str = Query(
        "created_at",
        description=(
            "Sort field: created_at, updated_at, due_date, "
            "priority, title, rank, smart_order"
        ),
    ),
    sort_dir: str = Query("desc", description="Sort direction: asc, desc"),
) -> WorkItemListResponse:
    """List tasks in a project with pagination and filters."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    effective_assignee = current_user.id if assigned_to_me else assignee_id

    tasks, total = await task_service.list_tasks(
        project_id=project_id,
        organization_id=organization_id,
        status=status,
        assignee_id=effective_assignee,
        reporter_id=reporter_id,
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


@router.get(
    "/tasks/{task_id}",
    response_model=WorkItemDetailResponse,
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
) -> WorkItemDetailResponse:
    """Retrieve full task details with authorization check."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await task_service.get_task_details(task_id, project_id=project_id)


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
    Update task fields (title, description, priority, assignee, due date, points).
    Enforces role permissions, assignee eligibility, and optimistic concurrency.
    """
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
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
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
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
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

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
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
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
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

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


# -------------------------------------------------------------
# Mentionable Members (Autocomplete Support)
# -------------------------------------------------------------


@router.get(
    "/mentionable-members",
    response_model=list[MentionableUserResponse],
    summary="List Mentionable Members",
)
async def list_mentionable_members(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
    search: str | None = Query(None, description="Optional name or email filter"),
) -> list[MentionableUserResponse]:
    """Retrieve users eligible for @-mentions in task comments within project."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await collab_service.get_mentionable_members(
        organization_id=organization_id,
        project_id=project_id,
        search=search,
    )


# -------------------------------------------------------------
# Comments Endpoints
# -------------------------------------------------------------


@router.post(
    "/tasks/{task_id}/comments",
    response_model=WorkItemCommentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Task Comment",
)
async def create_task_comment(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    request: CommentCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
) -> WorkItemCommentResponse:
    """Add a discussion comment with structured mentions to a task."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    proj_role = proj_membership.role if proj_membership else None
    if proj_role == "viewer" or org_membership.role in {"viewer", "guest"}:
        raise DomainException(
            code="FORBIDDEN",
            message="Viewers do not have permission to comment on tasks.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return await collab_service.create_comment(
        task_id=task_id,
        project_id=project_id,
        actor_id=current_user.id,
        request=request,
    )


@router.get(
    "/tasks/{task_id}/comments",
    response_model=WorkItemCommentListResponse,
    summary="List Task Comments",
)
async def list_task_comments(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
) -> WorkItemCommentListResponse:
    """Retrieve paginated discussion comments for a task in chronological order."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await collab_service.list_comments(
        task_id=task_id,
        project_id=project_id,
        page=page,
        page_size=page_size,
    )


@router.patch(
    "/tasks/{task_id}/comments/{comment_id}",
    response_model=WorkItemCommentResponse,
    summary="Update Task Comment",
)
async def update_task_comment(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    comment_id: uuid.UUID,
    request: CommentUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
) -> WorkItemCommentResponse:
    """Update a comment. Allowed for comment author or project/org admin."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    is_admin = can_manage_project(
        project, current_user.id, org_membership, proj_membership
    )
    return await collab_service.update_comment(
        task_id=task_id,
        project_id=project_id,
        comment_id=comment_id,
        actor_id=current_user.id,
        is_admin_or_lead=is_admin,
        request=request,
    )


@router.delete(
    "/tasks/{task_id}/comments/{comment_id}",
    response_model=MessageResponse,
    summary="Delete Task Comment",
)
async def delete_task_comment(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    comment_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    collab_service: Annotated[CollaborationService, Depends(get_collaboration_service)],
) -> MessageResponse:
    """Delete a comment. Allowed for comment author or project/org admin."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    is_admin = can_manage_project(
        project, current_user.id, org_membership, proj_membership
    )
    await collab_service.delete_comment(
        task_id=task_id,
        project_id=project_id,
        comment_id=comment_id,
        actor_id=current_user.id,
        is_admin_or_lead=is_admin,
    )
    return MessageResponse(message="Comment deleted successfully.")


# -------------------------------------------------------------
# Attachments Endpoints
# -------------------------------------------------------------


@router.post(
    "/tasks/{task_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Task Attachment",
)
async def upload_task_attachment(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    file: UploadFile = File(..., description="File to upload"),
    current_user: User = Depends(get_current_active_user),
    org_service: OrganizationService = Depends(get_organization_service),
    project_service: ProjectService = Depends(get_project_service),
    task_service: TaskService = Depends(get_task_service),
) -> AttachmentResponse:
    """Upload a file or image attachment for a work item."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
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
            message="You do not have permission to attach files to this task.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return await task_service.upload_attachment(
        task_id=task_id,
        project_id=project_id,
        actor_id=current_user.id,
        file=file,
    )


@router.get(
    "/tasks/{task_id}/attachments",
    response_model=AttachmentListResponse,
    summary="List Task Attachments",
)
async def list_task_attachments(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> AttachmentListResponse:
    """List all attachments associated with a work item."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await task_service.list_attachments(task_id=task_id, project_id=project_id)


@router.get(
    "/tasks/{task_id}/attachments/{attachment_id}",
    response_model=AttachmentResponse,
    summary="Get Attachment Details",
)
async def get_attachment_details(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    attachment_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> AttachmentResponse:
    """Retrieve attachment metadata."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await task_service.get_attachment(
        task_id=task_id,
        project_id=project_id,
        attachment_id=attachment_id,
    )


@router.get(
    "/tasks/{task_id}/attachments/{attachment_id}/download",
    summary="Download Task Attachment",
)
async def download_task_attachment(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    attachment_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> FileResponse:
    """Securely download or view an attachment file."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    file_path, attachment = await task_service.get_attachment_file(
        task_id=task_id,
        project_id=project_id,
        attachment_id=attachment_id,
    )
    return FileResponse(
        path=str(file_path),
        filename=attachment.file_name,
        media_type=attachment.content_type,
    )


@router.delete(
    "/tasks/{task_id}/attachments/{attachment_id}",
    response_model=MessageResponse,
    summary="Delete Task Attachment",
)
async def delete_task_attachment(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    attachment_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> MessageResponse:
    """Delete an attachment. Allowed for uploader or project/org admin."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    is_admin = can_manage_project(
        project, current_user.id, org_membership, proj_membership
    )
    await task_service.delete_attachment(
        task_id=task_id,
        project_id=project_id,
        attachment_id=attachment_id,
        actor_id=current_user.id,
        is_admin_or_lead=is_admin,
    )
    return MessageResponse(message="Attachment deleted successfully.")


# -------------------------------------------------------------
# Checklists Endpoints
# -------------------------------------------------------------


@router.post(
    "/tasks/{task_id}/checklists",
    response_model=ChecklistItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Checklist Item",
)
async def create_checklist_item(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    request: ChecklistItemCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> ChecklistItemResponse:
    """Create a checklist item under a task."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
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

    return await task_service.create_checklist_item(
        task_id=task_id,
        project_id=project_id,
        actor_id=current_user.id,
        request=request,
    )


@router.get(
    "/tasks/{task_id}/checklists",
    response_model=ChecklistListResponse,
    summary="List Checklist Items",
)
async def list_checklist_items(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> ChecklistListResponse:
    """List checklist items for a task ordered by position."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await task_service.list_checklist_items(
        task_id=task_id, project_id=project_id
    )


@router.patch(
    "/tasks/{task_id}/checklists/{checklist_id}",
    response_model=ChecklistItemResponse,
    summary="Update Checklist Item",
)
async def update_checklist_item(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    request: ChecklistItemUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> ChecklistItemResponse:
    """Update checklist item content, completion state, position, or assignee."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    task = await task_service.get_task_by_id(task_id, project_id=project_id)
    proj_role = proj_membership.role if proj_membership else None

    # Check permission
    can_mod = can_modify_task(
        project_lead_id=project.owner_id,
        org_role=org_membership.role,
        proj_role=proj_role,
        task_reporter_id=task.reporter_id,
        task_assignee_id=task.assignee_id,
        current_user_id=current_user.id,
    )
    if not can_mod:
        raise DomainException(
            code="FORBIDDEN",
            message="You do not have permission to modify this checklist item.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return await task_service.update_checklist_item(
        task_id=task_id,
        project_id=project_id,
        item_id=checklist_id,
        actor_id=current_user.id,
        request=request,
    )


@router.delete(
    "/tasks/{task_id}/checklists/{checklist_id}",
    response_model=MessageResponse,
    summary="Delete Checklist Item",
)
async def delete_checklist_item(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    checklist_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> MessageResponse:
    """Delete a checklist item."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
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
            message="You do not have permission to delete this checklist item.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await task_service.delete_checklist_item(
        task_id=task_id,
        project_id=project_id,
        item_id=checklist_id,
        actor_id=current_user.id,
    )
    return MessageResponse(message="Checklist item deleted successfully.")


# -------------------------------------------------------------
# Subtasks Endpoints
# -------------------------------------------------------------


@router.post(
    "/tasks/{task_id}/subtasks",
    response_model=WorkItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create Subtask",
)
async def create_subtask(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    request: SubtaskCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> WorkItemResponse:
    """Create a child subtask under an existing parent task."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    proj_role = proj_membership.role if proj_membership else None
    if proj_role == "viewer" or org_membership.role in {"viewer", "guest"}:
        raise DomainException(
            code="FORBIDDEN",
            message=(
                "Viewers do not have permission to create subtasks in this project."
            ),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return await task_service.create_subtask(
        parent_task_id=task_id,
        project_id=project_id,
        org_id=organization_id,
        creator_id=current_user.id,
        request=request,
    )


@router.get(
    "/tasks/{task_id}/subtasks",
    response_model=WorkItemListResponse,
    summary="List Subtasks",
)
async def list_subtasks(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(25, ge=1, le=100, description="Items per page"),
) -> WorkItemListResponse:
    """List child subtasks under a parent task with pagination."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await task_service.list_subtasks(
        task_id=task_id,
        project_id=project_id,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/tasks/bulk-operation",
    response_model=BulkTaskOperationResponse,
    summary="Execute batch operation on tasks",
)
async def bulk_task_operation(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: BulkTaskOperationRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> BulkTaskOperationResponse:
    """Execute status, assignment, priority, label, or delete operations in bulk."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(
        project_id, org_id=organization_id
    )
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    if request.operation == "delete" and not can_manage_project(
        project, current_user.id, org_membership, proj_membership
    ):
        raise DomainException(
            code="INSUFFICIENT_PERMISSIONS",
            message="Only project administrators can perform bulk deletions.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return await task_service.execute_bulk_operation(
        project_id=project_id,
        org_id=organization_id,
        actor_id=current_user.id,
        request=request,
    )
