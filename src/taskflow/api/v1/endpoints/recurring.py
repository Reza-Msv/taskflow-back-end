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
    RecurringTaskRuleCreateRequest,
    RecurringTaskRuleResponse,
    RecurringTaskRuleUpdateRequest,
    TriggerRecurrenceResponse,
    WorkItemResponse,
)
from taskflow.domains.work_items.service import TaskService

router = APIRouter(
    prefix="/organizations/{organization_id}/projects/{project_id}/recurring-tasks",
    tags=["Recurring Tasks"],
)


@router.post(
    "",
    response_model=RecurringTaskRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a recurring task rule",
)
async def create_recurring_task_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: RecurringTaskRuleCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> RecurringTaskRuleResponse:
    """Configure a schedule for recurring task generation."""
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
            message="Only project administrators can create recurring task rules.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    rule = await task_service.create_recurring_rule(
        project_id=project_id,
        org_id=organization_id,
        creator_id=current_user.id,
        request=request,
    )
    return RecurringTaskRuleResponse.model_validate(rule)


@router.get(
    "",
    response_model=list[RecurringTaskRuleResponse],
    summary="List recurring task rules",
)
async def list_recurring_task_rules(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    active_only: bool = Query(default=False, description="Filter active rules only"),
) -> list[RecurringTaskRuleResponse]:
    """Retrieve all recurring task schedules in a project."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    rules = await task_service.list_recurring_rules(
        project_id=project_id,
        org_id=organization_id,
        active_only=active_only,
    )
    return [RecurringTaskRuleResponse.model_validate(r) for r in rules]


@router.get(
    "/{rule_id}",
    response_model=RecurringTaskRuleResponse,
    summary="Get recurring task rule details",
)
async def get_recurring_task_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> RecurringTaskRuleResponse:
    """Fetch details of a single recurring task rule."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    rule = await task_service.get_recurring_rule(
        project_id=project_id,
        org_id=organization_id,
        rule_id=rule_id,
    )
    return RecurringTaskRuleResponse.model_validate(rule)


@router.patch(
    "/{rule_id}",
    response_model=RecurringTaskRuleResponse,
    summary="Update a recurring task rule",
)
async def update_recurring_task_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    request: RecurringTaskRuleUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> RecurringTaskRuleResponse:
    """Modify pattern, interval, or task template of a recurring rule."""
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
            message="Only project administrators can update recurring task rules.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    updated = await task_service.update_recurring_rule(
        project_id=project_id,
        org_id=organization_id,
        rule_id=rule_id,
        request=request,
    )
    return RecurringTaskRuleResponse.model_validate(updated)


@router.delete(
    "/{rule_id}",
    response_model=MessageResponse,
    summary="Delete a recurring task rule",
)
async def delete_recurring_task_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """Delete a recurring task rule from a project."""
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
            message="Only project administrators can delete recurring task rules.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await task_service.delete_recurring_rule(
        project_id=project_id,
        org_id=organization_id,
        rule_id=rule_id,
    )
    return MessageResponse(message="Recurring task rule successfully deleted.")


@router.post(
    "/{rule_id}/trigger",
    response_model=TriggerRecurrenceResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Manually trigger recurring task generation",
)
async def trigger_recurring_task_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> TriggerRecurrenceResponse:
    """Instantiate a task immediately from rule and advance next_run_at."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    task, rule = await task_service.trigger_recurring_rule(
        project_id=project_id,
        org_id=organization_id,
        actor_id=current_user.id,
        rule_id=rule_id,
    )
    return TriggerRecurrenceResponse(
        task=WorkItemResponse.model_validate(task),
        rule=RecurringTaskRuleResponse.model_validate(rule),
        message="Recurring task successfully triggered and next run scheduled.",
    )
