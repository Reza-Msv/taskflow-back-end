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
    InstantiateTemplateRequest,
    TaskTemplateCreateRequest,
    TaskTemplateResponse,
    TaskTemplateUpdateRequest,
    WorkItemResponse,
)
from taskflow.domains.work_items.service import TaskService

router = APIRouter(
    prefix="/organizations/{organization_id}/projects/{project_id}/templates",
    tags=["Task Templates"],
)


@router.post(
    "",
    response_model=TaskTemplateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a task template",
)
async def create_task_template(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: TaskTemplateCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TaskTemplateResponse:
    """Create a reusable task blueprint in a project."""
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
            message="Only project administrators can create task templates.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    template = await task_service.create_template(
        project_id=project_id,
        org_id=organization_id,
        creator_id=current_user.id,
        request=request,
    )
    return TaskTemplateResponse.model_validate(template)


@router.get(
    "",
    response_model=list[TaskTemplateResponse],
    summary="List task templates",
)
async def list_task_templates(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> list[TaskTemplateResponse]:
    """Retrieve all task templates in a project."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    templates = await task_service.list_templates(
        project_id=project_id,
        org_id=organization_id,
    )
    return [TaskTemplateResponse.model_validate(t) for t in templates]


@router.get(
    "/{template_id}",
    response_model=TaskTemplateResponse,
    summary="Get task template details",
)
async def get_task_template(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    template_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TaskTemplateResponse:
    """Fetch details of a single task template."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    template = await task_service.get_template(
        project_id=project_id,
        org_id=organization_id,
        template_id=template_id,
    )
    return TaskTemplateResponse.model_validate(template)


@router.patch(
    "/{template_id}",
    response_model=TaskTemplateResponse,
    summary="Update a task template",
)
async def update_task_template(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    template_id: uuid.UUID,
    request: TaskTemplateUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TaskTemplateResponse:
    """Update fields of an existing task template."""
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
            message="Only project administrators can update task templates.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    updated = await task_service.update_template(
        project_id=project_id,
        org_id=organization_id,
        template_id=template_id,
        request=request,
    )
    return TaskTemplateResponse.model_validate(updated)


@router.delete(
    "/{template_id}",
    response_model=MessageResponse,
    summary="Delete a task template",
)
async def delete_task_template(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    template_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """Remove a task template from a project."""
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
            message="Only project administrators can delete task templates.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await task_service.delete_template(
        project_id=project_id,
        org_id=organization_id,
        template_id=template_id,
    )
    return MessageResponse(message="Task template successfully deleted.")


@router.post(
    "/{template_id}/instantiate",
    response_model=WorkItemResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Instantiate a task from template",
)
async def instantiate_task_template(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    template_id: uuid.UUID,
    request: InstantiateTemplateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> WorkItemResponse:
    """Create a new task instance from a template blueprint."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    task = await task_service.instantiate_template(
        project_id=project_id,
        org_id=organization_id,
        actor_id=current_user.id,
        template_id=template_id,
        request=request,
    )
    return WorkItemResponse.model_validate(task)
