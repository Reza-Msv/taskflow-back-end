import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

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
    WorkflowStatusCreateRequest,
    WorkflowStatusDetailResponse,
    WorkflowStatusUpdateRequest,
    WorkflowTransitionCreateRequest,
    WorkflowTransitionResponse,
)
from taskflow.domains.work_items.service import TaskService

router = APIRouter(
    prefix="/organizations/{organization_id}/projects/{project_id}/workflows",
    tags=["Workflows"],
)


@router.post(
    "/statuses",
    response_model=WorkflowStatusDetailResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom workflow status",
)
async def create_workflow_status(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: WorkflowStatusCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> WorkflowStatusDetailResponse:
    """Create a project-scoped custom workflow column status."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )
    if not can_manage_project(
        project, current_user.id, org_membership, proj_membership
    ):
        raise DomainException(
            code="INSUFFICIENT_PERMISSIONS",
            message="Only project administrators can create workflow statuses.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return await task_service.create_workflow_status(
        project_id=project_id,
        org_id=organization_id,
        request=request,
    )


@router.get(
    "/statuses",
    response_model=list[WorkflowStatusDetailResponse],
    summary="List project workflow statuses",
)
async def list_workflow_statuses(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> list[WorkflowStatusDetailResponse]:
    """Retrieve all workflow statuses with permitted transition rules."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await task_service.list_workflow_statuses(
        project_id=project_id,
        org_id=organization_id,
    )


@router.patch(
    "/statuses/{status_id}",
    response_model=WorkflowStatusDetailResponse,
    summary="Update a workflow status",
)
async def update_workflow_status(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    status_id: uuid.UUID,
    request: WorkflowStatusUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> WorkflowStatusDetailResponse:
    """Modify display name, category, or order of a custom status."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )
    if not can_manage_project(
        project, current_user.id, org_membership, proj_membership
    ):
        raise DomainException(
            code="INSUFFICIENT_PERMISSIONS",
            message="Only project administrators can update workflow statuses.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return await task_service.update_workflow_status(
        project_id=project_id,
        org_id=organization_id,
        status_id=status_id,
        request=request,
    )


@router.delete(
    "/statuses/{status_id}",
    response_model=MessageResponse,
    summary="Delete a workflow status",
)
async def delete_workflow_status(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    status_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """Delete an unused custom workflow status."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )
    if not can_manage_project(
        project, current_user.id, org_membership, proj_membership
    ):
        raise DomainException(
            code="INSUFFICIENT_PERMISSIONS",
            message="Only project administrators can delete workflow statuses.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await task_service.delete_workflow_status(
        project_id=project_id,
        org_id=organization_id,
        status_id=status_id,
    )
    return MessageResponse(message="Workflow status successfully deleted.")


@router.post(
    "/transitions",
    response_model=WorkflowTransitionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workflow transition rule",
)
async def create_workflow_transition(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: WorkflowTransitionCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> WorkflowTransitionResponse:
    """Define a permitted transition rule between two workflow statuses."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )
    if not can_manage_project(
        project, current_user.id, org_membership, proj_membership
    ):
        raise DomainException(
            code="INSUFFICIENT_PERMISSIONS",
            message="Only project administrators can configure workflow transitions.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    return await task_service.create_workflow_transition(
        project_id=project_id,
        org_id=organization_id,
        request=request,
    )


@router.get(
    "/transitions",
    response_model=list[WorkflowTransitionResponse],
    summary="List project workflow transitions",
)
async def list_workflow_transitions(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> list[WorkflowTransitionResponse]:
    """Retrieve all permitted status transitions configured for a project."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await task_service.list_workflow_transitions(
        project_id=project_id,
        org_id=organization_id,
    )


@router.delete(
    "/transitions/{transition_id}",
    response_model=MessageResponse,
    summary="Delete a workflow transition rule",
)
async def delete_workflow_transition(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    transition_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """Remove a permitted workflow transition rule."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    proj_membership = await verify_project_access(
        project, current_user.id, org_membership, project_service
    )
    if not can_manage_project(
        project, current_user.id, org_membership, proj_membership
    ):
        raise DomainException(
            code="INSUFFICIENT_PERMISSIONS",
            message="Only project administrators can delete workflow transitions.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await task_service.delete_workflow_transition(
        project_id=project_id,
        org_id=organization_id,
        transition_id=transition_id,
    )
    return MessageResponse(message="Workflow transition successfully deleted.")
