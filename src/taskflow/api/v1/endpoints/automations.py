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
    AutomationDryRunRequest,
    AutomationDryRunResponse,
    AutomationLogResponse,
    AutomationRuleCreateRequest,
    AutomationRuleResponse,
    AutomationRuleUpdateRequest,
)
from taskflow.domains.work_items.service import TaskService

router = APIRouter(
    prefix="/organizations/{organization_id}/projects/{project_id}/automations",
    tags=["Automations"],
)


@router.post(
    "/rules",
    response_model=AutomationRuleResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an automation rule",
)
async def create_automation_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    request: AutomationRuleCreateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> AutomationRuleResponse:
    """Configure an event-driven automation rule for a project."""
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
            message="Only project administrators can create automation rules.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    rule = await task_service.create_automation_rule(
        project_id=project_id,
        org_id=organization_id,
        creator_id=current_user.id,
        request=request,
    )
    return AutomationRuleResponse.model_validate(rule)


@router.get(
    "/rules",
    response_model=list[AutomationRuleResponse],
    summary="List automation rules",
)
async def list_automation_rules(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    trigger_event: str | None = Query(
        default=None,
        description="Filter by trigger event (e.g. task_status_changed)",
    ),
) -> list[AutomationRuleResponse]:
    """Retrieve all automation rules defined for a project."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    rules = await task_service.list_automation_rules(
        project_id=project_id,
        org_id=organization_id,
        trigger_event=trigger_event,
    )
    return [AutomationRuleResponse.model_validate(r) for r in rules]


@router.get(
    "/rules/{rule_id}",
    response_model=AutomationRuleResponse,
    summary="Get automation rule details",
)
async def get_automation_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> AutomationRuleResponse:
    """Fetch details of a single automation rule."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    rule = await task_service.get_automation_rule(
        project_id=project_id,
        org_id=organization_id,
        rule_id=rule_id,
    )
    return AutomationRuleResponse.model_validate(rule)


@router.patch(
    "/rules/{rule_id}",
    response_model=AutomationRuleResponse,
    summary="Update an automation rule",
)
async def update_automation_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    request: AutomationRuleUpdateRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> AutomationRuleResponse:
    """Update triggers, conditions, or actions of an automation rule."""
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
            message="Only project administrators can update automation rules.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    updated = await task_service.update_automation_rule(
        project_id=project_id,
        org_id=organization_id,
        rule_id=rule_id,
        request=request,
    )
    return AutomationRuleResponse.model_validate(updated)


@router.delete(
    "/rules/{rule_id}",
    response_model=MessageResponse,
    summary="Delete an automation rule",
)
async def delete_automation_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> MessageResponse:
    """Delete an automation rule from a project."""
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
            message="Only project administrators can delete automation rules.",
            status_code=status.HTTP_403_FORBIDDEN,
        )

    await task_service.delete_automation_rule(
        project_id=project_id,
        org_id=organization_id,
        rule_id=rule_id,
    )
    return MessageResponse(message="Automation rule successfully deleted.")


@router.post(
    "/rules/{rule_id}/test",
    response_model=AutomationDryRunResponse,
    summary="Dry-run / test an automation rule against a task",
)
async def dry_run_automation_rule(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    rule_id: uuid.UUID,
    request: AutomationDryRunRequest,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
) -> AutomationDryRunResponse:
    """Simulate automation rule execution on a task without persisting modifications."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    return await task_service.dry_run_automation(
        project_id=project_id,
        org_id=organization_id,
        rule_id=rule_id,
        request=request,
    )


@router.get(
    "/logs",
    response_model=list[AutomationLogResponse],
    summary="List automation execution audit logs",
)
async def list_automation_logs(
    organization_id: uuid.UUID,
    project_id: uuid.UUID,
    current_user: Annotated[User, Depends(get_current_active_user)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
    project_service: Annotated[ProjectService, Depends(get_project_service)],
    org_service: Annotated[OrganizationService, Depends(get_organization_service)],
    rule_id: uuid.UUID | None = Query(
        default=None,
        description="Optional filter by automation rule ID",
    ),
    limit: int = Query(default=50, ge=1, le=100, description="Max logs returned"),
) -> list[AutomationLogResponse]:
    """Retrieve audit history logs of executed automation rules."""
    org_membership = await get_org_membership(
        organization_id, current_user.id, org_service
    )
    project = await project_service.get_project_by_id(project_id, organization_id)
    await verify_project_access(
        project, current_user.id, org_membership, project_service
    )

    logs = await task_service.list_automation_logs(
        project_id=project_id,
        org_id=organization_id,
        rule_id=rule_id,
        limit=limit,
    )
    return [AutomationLogResponse.model_validate(log) for log in logs]
