import math
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.config import settings
from taskflow.core.exceptions import DomainException
from taskflow.domains.collaboration.models import Notification
from taskflow.domains.collaboration.repository import CollaborationRepository
from taskflow.domains.collaboration.websocket import (
    NotificationBroadcaster,
    get_notification_broadcaster,
)
from taskflow.domains.identity.repository import UserRepository
from taskflow.domains.organizations.repository import OrganizationRepository
from taskflow.domains.projects.models import Project
from taskflow.domains.projects.repository import ProjectRepository
from taskflow.domains.work_items.models import (
    Attachment,
    AutomationLog,
    AutomationRule,
    RecurringTaskRule,
    SavedFilter,
    TaskTemplate,
    WorkItem,
)
from taskflow.domains.work_items.repository import (
    COMPLETED_STATUSES,
    DashboardRepository,
    SavedFilterRepository,
    WorkItemRepository,
)
from taskflow.domains.work_items.schemas import (
    AttachmentListResponse,
    AttachmentResponse,
    AutomationDryRunRequest,
    AutomationDryRunResponse,
    AutomationRuleCreateRequest,
    AutomationRuleUpdateRequest,
    BulkItemError,
    BulkTaskOperationRequest,
    BulkTaskOperationResponse,
    CalendarResponse,
    CalendarTaskItem,
    ChecklistItemCreateRequest,
    ChecklistItemResponse,
    ChecklistItemUpdateRequest,
    ChecklistListResponse,
    DashboardSummaryResponse,
    GlobalSearchResponse,
    InstantiateTemplateRequest,
    KanbanBoardResponse,
    KanbanColumn,
    MilestoneBrief,
    MilestoneCreateRequest,
    MilestoneListResponse,
    MilestoneResponse,
    MilestoneUpdateRequest,
    OverdueUpcomingTasksResponse,
    ProjectBrief,
    ProjectSummary,
    RecurringTaskRuleCreateRequest,
    RecurringTaskRuleUpdateRequest,
    SavedFilterCreateRequest,
    SavedFilterUpdateRequest,
    SearchProjectResult,
    SearchTaskResult,
    SubtaskCreateRequest,
    TaskDependenciesSummary,
    TaskDependencyCreateRequest,
    TaskDependencyListResponse,
    TaskDependencyResponse,
    TaskFilterCriteria,
    TaskStatusMoveRequest,
    TaskTemplateCreateRequest,
    TaskTemplateUpdateRequest,
    TimelineDependencyItem,
    TimelineMilestoneItem,
    TimelineResponse,
    TimelineTaskItem,
    UserActivityActor,
    UserActivityItem,
    UserActivityListResponse,
    WorkflowStatusCreateRequest,
    WorkflowStatusDetailResponse,
    WorkflowStatusResponse,
    WorkflowStatusUpdateRequest,
    WorkflowTransitionCreateRequest,
    WorkflowTransitionResponse,
    WorkItemActivityResponse,
    WorkItemCreateRequest,
    WorkItemDashboardItem,
    WorkItemDetailResponse,
    WorkItemListDashboardResponse,
    WorkItemListResponse,
    WorkItemResponse,
    WorkItemUpdateRequest,
)
from taskflow.domains.work_items.storage import default_storage


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Ensure a datetime is timezone-aware and normalized to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class DashboardService:
    """
    Business service orchestrating user dashboard metrics, task lists, and activity.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = DashboardRepository(session)

    @staticmethod
    def _to_dashboard_item(item: WorkItem, now: datetime) -> WorkItemDashboardItem:
        """Helper to map WorkItem model to WorkItemDashboardItem schema."""
        is_completed = item.status.lower() in COMPLETED_STATUSES
        due_date = item.due_date
        if due_date is not None and due_date.tzinfo is None:
            due_date = due_date.replace(tzinfo=UTC)
        is_overdue = bool(due_date and due_date < now and not is_completed)

        project_brief = ProjectBrief(
            id=item.project.id,
            name=item.project.name,
            key=item.project.key,
            organization_id=item.project.organization_id,
            is_private=item.project.is_private,
        )

        return WorkItemDashboardItem(
            id=item.id,
            issue_key=item.issue_key,
            type=item.type,
            title=item.title,
            status=item.status,
            priority=item.priority,
            due_date=item.due_date,
            estimated_points=item.estimated_points,
            rank=item.rank,
            created_at=item.created_at,
            updated_at=item.updated_at,
            project=project_brief,
            is_overdue=is_overdue,
        )

    async def get_dashboard_summary(
        self, user_id: uuid.UUID
    ) -> DashboardSummaryResponse:
        """
        Produce aggregated dashboard summary for authenticated user:
        - Accessible projects with role and counts
        - Task counts grouped by status
        - Assigned, in-progress, completed, and overdue metrics
        """
        now = datetime.now(UTC)

        # Projects summary
        project_rows = await self.repo.get_accessible_projects(user_id)
        project_summaries = [
            ProjectSummary(
                id=proj.id,
                name=proj.name,
                key=proj.key,
                organization_id=proj.organization_id,
                is_private=proj.is_private,
                role=role,
                total_tasks=total_tasks,
                assigned_tasks=assigned_tasks,
            )
            for proj, role, total_tasks, assigned_tasks in project_rows
        ]

        # Task metrics
        metrics = await self.repo.get_user_task_metrics(user_id, now)

        return DashboardSummaryResponse(
            total_projects=len(project_summaries),
            projects=project_summaries,
            status_counts=metrics["status_counts"],
            assigned_tasks_count=metrics["total_assigned"],
            in_progress_tasks_count=metrics["in_progress_count"],
            completed_tasks_count=metrics["completed_count"],
            overdue_tasks_count=metrics["overdue_count"],
        )

    async def get_my_tasks(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
        status: str | None = None,
        project_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        priority: str | None = None,
        type: str | None = None,
        search: str | None = None,
        due_date_from: datetime | None = None,
        due_date_to: datetime | None = None,
        is_overdue: bool | None = None,
        due_soon: bool | None = None,
        is_completed: bool | None = None,
        sort_by: str = "smart_order",
        sort_dir: str = "asc",
    ) -> WorkItemListDashboardResponse:
        """
        Get tasks assigned to user with filtering, pagination, and smart ordering.
        """
        if due_date_from and due_date_to and due_date_from > due_date_to:
            raise DomainException(
                code="INVALID_DATE_RANGE",
                message="due_date_from cannot be after due_date_to.",
                status_code=400,
                details={
                    "due_date_from": str(due_date_from),
                    "due_date_to": str(due_date_to),
                },
            )

        now = datetime.now(UTC)
        skip = (page - 1) * page_size
        items, total = await self.repo.list_assigned_tasks(
            user_id=user_id,
            now=now,
            skip=skip,
            limit=page_size,
            status=status,
            project_id=project_id,
            organization_id=organization_id,
            priority=priority,
            type=type,
            search=search,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
            is_overdue=is_overdue,
            due_soon=due_soon,
            is_completed=is_completed,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        dtos = [self._to_dashboard_item(item, now) for item in items]
        total_pages = math.ceil(total / page_size) if page_size > 0 else 0

        return WorkItemListDashboardResponse(
            items=dtos,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )

    async def get_overdue_and_upcoming_tasks(
        self,
        user_id: uuid.UUID,
        upcoming_days: int = 7,
        limit_per_group: int = 20,
    ) -> OverdueUpcomingTasksResponse:
        """
        Retrieve overdue tasks and tasks approaching due date within `upcoming_days`.
        """
        now = datetime.now(UTC)
        overdue_items, upcoming_items = await self.repo.get_overdue_and_upcoming_tasks(
            user_id=user_id,
            now=now,
            upcoming_days=upcoming_days,
            limit_per_group=limit_per_group,
        )

        overdue_dtos = [self._to_dashboard_item(item, now) for item in overdue_items]
        upcoming_dtos = [self._to_dashboard_item(item, now) for item in upcoming_items]

        return OverdueUpcomingTasksResponse(
            overdue_tasks=overdue_dtos,
            upcoming_tasks=upcoming_dtos,
        )

    async def get_recent_activity(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> UserActivityListResponse:
        """
        Return real recorded activity stream items for user.
        """
        skip = (page - 1) * page_size
        activities, total = await self.repo.list_recent_activity(
            user_id=user_id,
            skip=skip,
            limit=page_size,
        )

        items = []
        for act in activities:
            actor_data = act.get("actor")
            actor = (
                UserActivityActor(
                    id=actor_data["id"],
                    email=actor_data["email"],
                    full_name=actor_data["full_name"],
                )
                if actor_data
                else None
            )

            items.append(
                UserActivityItem(
                    id=act["id"],
                    activity_type=act["activity_type"],
                    field=act["field"],
                    old_value=act["old_value"],
                    new_value=act["new_value"],
                    created_at=act["created_at"],
                    work_item_id=act["work_item_id"],
                    work_item_key=act["work_item_key"],
                    work_item_title=act["work_item_title"],
                    project_id=act["project_id"],
                    project_name=act["project_name"],
                    actor=actor,
                    details=act["details"],
                )
            )

        total_pages = math.ceil(total / page_size) if page_size > 0 else 0

        return UserActivityListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )


class TaskService:
    """
    Business service managing task CRUD, assignment, Kanban workflows,
    status transitions, and activity history.
    """

    def __init__(
        self,
        session: AsyncSession,
        broadcaster: NotificationBroadcaster | None = None,
    ) -> None:
        self.session = session
        self.work_item_repo = WorkItemRepository(session)
        self.project_repo = ProjectRepository(session)
        self.org_repo = OrganizationRepository(session)
        self.user_repo = UserRepository(session)
        self.collab_repo = CollaborationRepository(session)
        self.broadcaster = broadcaster or get_notification_broadcaster()

    async def get_task_by_id(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
    ) -> WorkItem:
        """Fetch task or raise TASK_NOT_FOUND."""
        task = await self.work_item_repo.get_by_id(task_id, project_id=project_id)
        if task is None:
            raise DomainException(
                code="TASK_NOT_FOUND",
                message="Task not found or inaccessible.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"task_id": str(task_id)},
            )
        return task

    async def list_tasks(
        self,
        project_id: uuid.UUID | None = None,
        organization_id: uuid.UUID | None = None,
        status: str | None = None,
        assignee_id: uuid.UUID | None = None,
        reporter_id: uuid.UUID | None = None,
        priority: str | None = None,
        type: str | None = None,
        search: str | None = None,
        due_date_from: datetime | None = None,
        due_date_to: datetime | None = None,
        is_overdue: bool | None = None,
        due_soon: bool | None = None,
        is_completed: bool | None = None,
        label_id: uuid.UUID | None = None,
        sort_by: str = "created_at",
        sort_dir: str = "desc",
        page: int = 1,
        page_size: int = 25,
        accessible_filter: Any = None,
    ) -> tuple[list[WorkItem], int]:
        """List tasks with advanced filters, pagination, and sorting."""
        if due_date_from and due_date_to and due_date_from > due_date_to:
            raise DomainException(
                code="INVALID_DATE_RANGE",
                message="due_date_from cannot be after due_date_to.",
                status_code=400,
                details={
                    "due_date_from": str(due_date_from),
                    "due_date_to": str(due_date_to),
                },
            )

        skip = (page - 1) * page_size
        return await self.work_item_repo.list_work_items(
            project_id=project_id,
            organization_id=organization_id,
            status=status,
            assignee_id=assignee_id,
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
            skip=skip,
            limit=page_size,
            accessible_filter=accessible_filter,
        )

    async def get_my_tasks(
        self,
        user_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        status: str | None = None,
        priority: str | None = None,
        type: str | None = None,
        search: str | None = None,
        due_date_from: datetime | None = None,
        due_date_to: datetime | None = None,
        is_overdue: bool | None = None,
        due_soon: bool | None = None,
        is_completed: bool | None = None,
        label_id: uuid.UUID | None = None,
        sort_by: str = "smart_order",
        sort_dir: str = "asc",
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[WorkItem], int]:
        """
        List tasks assigned to authenticated user with personal filters
        and access check.
        """
        accessible_filter = DashboardRepository(
            self.session
        )._accessible_projects_subquery(user_id)

        return await self.list_tasks(
            project_id=project_id,
            organization_id=organization_id,
            status=status,
            assignee_id=user_id,
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
            accessible_filter=accessible_filter,
        )

    async def global_search(
        self,
        user_id: uuid.UUID,
        query: str,
        search_type: str = "all",
        organization_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> GlobalSearchResponse:
        """Global search across accessible tasks and projects for user."""
        clean_q = query.strip()
        if not clean_q:
            return GlobalSearchResponse(
                query="",
                tasks=[],
                projects=[],
                total_tasks=0,
                total_projects=0,
                limit=limit,
                offset=offset,
            )

        if organization_id is not None:
            membership = await self.org_repo.get_membership(organization_id, user_id)
            if membership is None or not membership.is_active:
                raise DomainException(
                    code="NOT_AN_ORGANIZATION_MEMBER",
                    message="You are not an active member of this organization.",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

        if project_id is not None:
            project = await self.project_repo.get_by_id(
                project_id, org_id=organization_id
            )
            if project is None:
                raise DomainException(
                    code="PROJECT_NOT_FOUND",
                    message="Project not found or inaccessible.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

        accessible_filter = DashboardRepository(
            self.session
        )._accessible_projects_subquery(user_id)

        task_results: list[SearchTaskResult] = []
        total_tasks = 0
        if search_type in ("all", "tasks"):
            task_rows, total_tasks = await self.work_item_repo.search_tasks(
                query=clean_q,
                accessible_filter=accessible_filter,
                organization_id=organization_id,
                project_id=project_id,
                limit=limit,
                offset=offset,
            )
            for item, matched_field in task_rows:
                task_results.append(
                    SearchTaskResult(
                        id=item.id,
                        issue_key=item.issue_key,
                        title=item.title,
                        description=item.description,
                        status=item.status,
                        priority=item.priority,
                        type=item.type,
                        project_id=item.project.id,
                        project_name=item.project.name,
                        project_key=item.project.key,
                        organization_id=item.organization_id,
                        matched_field=matched_field,
                    )
                )

        project_results: list[SearchProjectResult] = []
        total_projects = 0
        if search_type in ("all", "projects") and project_id is None:
            proj_rows, total_projects = await self.work_item_repo.search_projects(
                query=clean_q,
                accessible_filter=accessible_filter,
                organization_id=organization_id,
                limit=limit,
                offset=offset,
            )
            for proj, matched_field in proj_rows:
                project_results.append(
                    SearchProjectResult(
                        id=proj.id,
                        name=proj.name,
                        key=proj.key,
                        description=proj.description,
                        organization_id=proj.organization_id,
                        is_private=proj.is_private,
                        matched_field=matched_field,
                    )
                )

        return GlobalSearchResponse(
            query=clean_q,
            tasks=task_results,
            projects=project_results,
            total_tasks=total_tasks,
            total_projects=total_projects,
            limit=limit,
            offset=offset,
        )

    async def get_project_or_404(
        self, project_id: uuid.UUID, org_id: uuid.UUID
    ) -> Project:
        """Fetch project ensuring organization boundary isolation."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found or inaccessible in this organization.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"project_id": str(project_id)},
            )
        return project

    async def create_task(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        creator_id: uuid.UUID,
        request: WorkItemCreateRequest,
    ) -> WorkItem:
        """
        Create a new task, allocate next gapless sequence issue key,
        validate assignee and status, and record creation activity.
        """
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        # Validate assignee belongs to organization
        if request.assignee_id is not None:
            org_member = await self.org_repo.get_membership(org_id, request.assignee_id)
            if org_member is None or not org_member.is_active:
                raise DomainException(
                    code="INVALID_ASSIGNEE",
                    message="Assignee must be an active member of the organization.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"assignee_id": str(request.assignee_id)},
                )

        # Ensure default statuses exist for project
        await self.work_item_repo.ensure_default_workflow_statuses(project_id)

        # Resolve initial status
        initial_status = "todo"
        if request.status:
            status_obj = await self.work_item_repo.get_status_by_name_or_id(
                project_id, request.status
            )
            if status_obj is None:
                raise DomainException(
                    code="INVALID_STATUS",
                    message=(
                        f"Status '{request.status}' is not configured for this project."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"status": request.status},
                )
            initial_status = (
                status_obj.category.lower()
                if status_obj.category
                else status_obj.name.lower().replace(" ", "_")
            )
        else:
            # Check default status
            project_statuses = await self.work_item_repo.get_project_statuses(
                project_id
            )
            default_st = next((s for s in project_statuses if s.is_default), None)
            if default_st:
                initial_status = (
                    default_st.category.lower()
                    if default_st.category
                    else default_st.name.lower().replace(" ", "_")
                )

        # Validate parent if provided
        if request.parent_id is not None:
            parent = await self.work_item_repo.get_by_id(request.parent_id)
            if parent is None:
                raise DomainException(
                    code="PARENT_TASK_NOT_FOUND",
                    message="Parent task not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    details={"parent_id": str(request.parent_id)},
                )
            if parent.project_id != project_id or parent.organization_id != org_id:
                raise DomainException(
                    code="CROSS_PROJECT_HIERARCHY",
                    message=(
                        "Parent task must belong to the same project and organization."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"parent_id": str(request.parent_id)},
                )

        # Validate milestone if provided
        if request.milestone_id is not None:
            milestone = await self.work_item_repo.get_milestone_by_id(
                request.milestone_id, project_id=project_id
            )
            if milestone is None:
                raise DomainException(
                    code="MILESTONE_NOT_FOUND",
                    message="Milestone not found in this project.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    details={"milestone_id": str(request.milestone_id)},
                )

        # Atomically increment issue sequence counter
        seq_num, issue_key = await self.work_item_repo.get_next_sequence_and_issue_key(
            project_id=project.id,
            project_key=project.key,
        )

        rank_val = request.rank or f"0|{seq_num:06d}:"

        task = await self.work_item_repo.create_work_item(
            project_id=project_id,
            organization_id=org_id,
            sequence_number=seq_num,
            issue_key=issue_key,
            title=request.title,
            description=request.description,
            type=request.type,
            priority=request.priority,
            status=initial_status,
            reporter_id=creator_id,
            assignee_id=request.assignee_id,
            parent_id=request.parent_id,
            sprint_id=request.sprint_id,
            due_date=request.due_date,
            start_date=request.start_date,
            milestone_id=request.milestone_id,
            estimated_points=request.estimated_points,
            rank=rank_val,
        )

        if initial_status.lower() in COMPLETED_STATUSES:
            task.completed_at = datetime.now(UTC)

        # Audit creation event in same transaction
        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=creator_id,
            field="created",
            old_value=None,
            new_value=f"Created task {issue_key}",
        )

        # Notification on task assignment
        assigned_notif = None
        if request.assignee_id is not None and request.assignee_id != creator_id:
            assigned_notif = await self.collab_repo.create_notification(
                user_id=request.assignee_id,
                actor_id=creator_id,
                organization_id=org_id,
                type="task_assigned",
                title=f"Assigned to {issue_key}",
                message=f"You were assigned to task {issue_key}: {task.title}",
                resource_type="task",
                resource_id=task.id,
            )

        await self.session.commit()
        await self.session.refresh(task)

        if assigned_notif:
            await self.broadcaster.broadcast_to_user(
                assigned_notif.user_id,
                "notification_created",
                {
                    "id": str(assigned_notif.id),
                    "user_id": str(assigned_notif.user_id),
                    "actor_id": (
                        str(assigned_notif.actor_id)
                        if assigned_notif.actor_id
                        else None
                    ),
                    "organization_id": (
                        str(assigned_notif.organization_id)
                        if assigned_notif.organization_id
                        else None
                    ),
                    "type": assigned_notif.type,
                    "title": assigned_notif.title,
                    "message": assigned_notif.message,
                    "resource_type": assigned_notif.resource_type,
                    "resource_id": (
                        str(assigned_notif.resource_id)
                        if assigned_notif.resource_id
                        else None
                    ),
                    "is_read": assigned_notif.is_read,
                    "created_at": assigned_notif.created_at.isoformat(),
                    "updated_at": assigned_notif.updated_at.isoformat(),
                },
            )

        # Trigger event automations
        await self.execute_automations(
            project_id=project_id,
            trigger_event="task_created",
            task=task,
            actor_id=creator_id,
            depth=0,
        )
        if task.assignee_id:
            await self.execute_automations(
                project_id=project_id,
                trigger_event="task_assigned",
                task=task,
                actor_id=creator_id,
                depth=0,
            )
        if task.status.lower() in COMPLETED_STATUSES:
            await self.execute_automations(
                project_id=project_id,
                trigger_event="task_completed",
                task=task,
                actor_id=creator_id,
                depth=0,
            )

        return task

    async def update_task(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: WorkItemUpdateRequest,
    ) -> WorkItem:
        """
        Update task fields with optimistic concurrency control,
        assignee validation, and granular audit history.
        """
        task = await self.get_task_by_id(task_id, project_id=project_id)

        # Optimistic concurrency check
        if request.version is not None and task.version != request.version:
            raise DomainException(
                code="STALE_OBJECT_ERROR",
                message=(
                    "The task has been modified by another transaction. Please reload."
                ),
                status_code=status.HTTP_409_CONFLICT,
                details={
                    "current_version": task.version,
                    "provided_version": request.version,
                },
            )

        # Validate new assignee
        old_assignee_id = task.assignee_id
        if request.assignee_id is not None and request.assignee_id != task.assignee_id:
            org_member = await self.org_repo.get_membership(
                task.organization_id, request.assignee_id
            )
            if org_member is None or not org_member.is_active:
                raise DomainException(
                    code="INVALID_ASSIGNEE",
                    message="Assignee must be an active member of the organization.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"assignee_id": str(request.assignee_id)},
                )

        mutated = False
        assigned_notif = None

        if request.title is not None and request.title.strip() != task.title:
            await self.work_item_repo.record_activity(
                task.id, actor_id, "title", task.title, request.title.strip()
            )
            task.title = request.title.strip()
            mutated = True

        if request.description is not None and request.description.strip() != (
            task.description or ""
        ):
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "description",
                task.description,
                request.description.strip(),
            )
            task.description = request.description.strip()
            mutated = True

        if request.priority is not None and request.priority != task.priority:
            await self.work_item_repo.record_activity(
                task.id, actor_id, "priority", task.priority, request.priority
            )
            task.priority = request.priority
            mutated = True

        if request.assignee_id is not None and request.assignee_id != task.assignee_id:
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "assignee_id",
                str(task.assignee_id) if task.assignee_id else None,
                str(request.assignee_id),
            )
            task.assignee_id = request.assignee_id
            mutated = True
            if request.assignee_id != actor_id:
                assigned_notif = await self.collab_repo.create_notification(
                    user_id=request.assignee_id,
                    actor_id=actor_id,
                    organization_id=task.organization_id,
                    type="task_assigned",
                    title=f"Assigned to {task.issue_key}",
                    message=f"You were assigned to task {task.issue_key}: {task.title}",
                    resource_type="task",
                    resource_id=task.id,
                )

        if request.parent_id is not None and request.parent_id != task.parent_id:
            if request.parent_id == task.id:
                raise DomainException(
                    code="INVALID_PARENT",
                    message="Task cannot be its own parent.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"task_id": str(task.id)},
                )
            parent = await self.work_item_repo.get_by_id(request.parent_id)
            if parent is None:
                raise DomainException(
                    code="PARENT_TASK_NOT_FOUND",
                    message="Parent task not found.",
                    status_code=status.HTTP_404_NOT_FOUND,
                    details={"parent_id": str(request.parent_id)},
                )
            if (
                parent.project_id != task.project_id
                or parent.organization_id != task.organization_id
            ):
                raise DomainException(
                    code="CROSS_PROJECT_HIERARCHY",
                    message=(
                        "Parent task must belong to the same project and organization."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"parent_id": str(request.parent_id)},
                )
            is_circular = await self.work_item_repo.check_circular_parent(
                task.id, request.parent_id
            )
            if is_circular:
                raise DomainException(
                    code="CIRCULAR_HIERARCHY",
                    message=(
                        "Setting this parent would create a circular dependency cycle."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"parent_id": str(request.parent_id)},
                )
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "parent_id",
                str(task.parent_id) if task.parent_id else None,
                str(request.parent_id),
            )
            task.parent_id = request.parent_id
            mutated = True

        # Validate date consistency if either start_date or due_date is updated
        new_start = (
            request.start_date
            if "start_date" in request.model_fields_set
            else task.start_date
        )
        new_due = (
            request.due_date
            if "due_date" in request.model_fields_set
            else task.due_date
        )
        norm_start = _ensure_utc(new_start)
        norm_due = _ensure_utc(new_due)
        if norm_start is not None and norm_due is not None and norm_due < norm_start:
            raise DomainException(
                code="INVALID_DATES",
                message="due_date cannot be earlier than start_date.",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "start_date": norm_start.isoformat(),
                    "due_date": norm_due.isoformat(),
                },
            )

        if (
            "start_date" in request.model_fields_set
            and request.start_date != task.start_date
        ):
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "start_date",
                task.start_date.isoformat() if task.start_date else None,
                request.start_date.isoformat() if request.start_date else None,
            )
            task.start_date = request.start_date
            mutated = True

        due_date_notif = None
        if "due_date" in request.model_fields_set and request.due_date != task.due_date:
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "due_date",
                task.due_date.isoformat() if task.due_date else None,
                request.due_date.isoformat() if request.due_date else None,
            )
            task.due_date = request.due_date
            mutated = True
            # Notify assignee if due date changed and not already notified
            is_already_notified = (
                assigned_notif is not None
                and assigned_notif.user_id == task.assignee_id
            )
            if (
                task.assignee_id
                and task.assignee_id != actor_id
                and not is_already_notified
                and request.due_date is not None
            ):
                due_str = request.due_date.strftime("%Y-%m-%d")
                due_date_notif = await self.collab_repo.create_notification(
                    user_id=task.assignee_id,
                    actor_id=actor_id,
                    organization_id=task.organization_id,
                    type="task_due_date_changed",
                    title=f"Due Date Changed on {task.issue_key}",
                    message=(
                        f"Due date for task {task.issue_key} was updated to {due_str}"
                    ),
                    resource_type="task",
                    resource_id=task.id,
                )

        if (
            "milestone_id" in request.model_fields_set
            and request.milestone_id != task.milestone_id
        ):
            if request.milestone_id is not None:
                milestone = await self.work_item_repo.get_milestone_by_id(
                    request.milestone_id, project_id=task.project_id
                )
                if milestone is None:
                    raise DomainException(
                        code="MILESTONE_NOT_FOUND",
                        message="Milestone not found in this project.",
                        status_code=status.HTTP_404_NOT_FOUND,
                        details={"milestone_id": str(request.milestone_id)},
                    )
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "milestone_id",
                str(task.milestone_id) if task.milestone_id else None,
                str(request.milestone_id) if request.milestone_id else None,
            )
            task.milestone_id = request.milestone_id
            mutated = True

        if (
            request.estimated_points is not None
            and request.estimated_points != task.estimated_points
        ):
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "estimated_points",
                str(task.estimated_points)
                if task.estimated_points is not None
                else None,
                str(request.estimated_points),
            )
            task.estimated_points = request.estimated_points
            mutated = True

        if (
            "sprint_id" in request.model_fields_set
            and request.sprint_id != task.sprint_id
        ):
            old_sprint_val = str(task.sprint_id) if task.sprint_id else None
            new_sprint_val = str(request.sprint_id) if request.sprint_id else None
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "sprint_id",
                old_sprint_val,
                new_sprint_val,
            )
            task.sprint_id = request.sprint_id
            mutated = True

        if request.rank is not None and request.rank != task.rank:
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "rank",
                task.rank,
                request.rank,
            )
            task.rank = request.rank
            mutated = True

        if mutated:
            task.version += 1
            await self.session.commit()
            await self.session.refresh(task)

            if assigned_notif:
                await self.broadcaster.broadcast_to_user(
                    assigned_notif.user_id,
                    "notification_created",
                    {
                        "id": str(assigned_notif.id),
                        "user_id": str(assigned_notif.user_id),
                        "actor_id": (
                            str(assigned_notif.actor_id)
                            if assigned_notif.actor_id
                            else None
                        ),
                        "organization_id": (
                            str(assigned_notif.organization_id)
                            if assigned_notif.organization_id
                            else None
                        ),
                        "type": assigned_notif.type,
                        "title": assigned_notif.title,
                        "message": assigned_notif.message,
                        "resource_type": assigned_notif.resource_type,
                        "resource_id": (
                            str(assigned_notif.resource_id)
                            if assigned_notif.resource_id
                            else None
                        ),
                        "is_read": assigned_notif.is_read,
                        "created_at": assigned_notif.created_at.isoformat(),
                        "updated_at": assigned_notif.updated_at.isoformat(),
                    },
                )

            if due_date_notif:
                await self.broadcaster.broadcast_to_user(
                    due_date_notif.user_id,
                    "notification_created",
                    {
                        "id": str(due_date_notif.id),
                        "user_id": str(due_date_notif.user_id),
                        "actor_id": (
                            str(due_date_notif.actor_id)
                            if due_date_notif.actor_id
                            else None
                        ),
                        "organization_id": (
                            str(due_date_notif.organization_id)
                            if due_date_notif.organization_id
                            else None
                        ),
                        "type": due_date_notif.type,
                        "title": due_date_notif.title,
                        "message": due_date_notif.message,
                        "resource_type": due_date_notif.resource_type,
                        "resource_id": (
                            str(due_date_notif.resource_id)
                            if due_date_notif.resource_id
                            else None
                        ),
                        "is_read": due_date_notif.is_read,
                        "created_at": due_date_notif.created_at.isoformat(),
                        "updated_at": due_date_notif.updated_at.isoformat(),
                    },
                )

        if request.assignee_id is not None and request.assignee_id != old_assignee_id:
            await self.execute_automations(
                project_id=project_id,
                trigger_event="task_assigned",
                task=task,
                actor_id=actor_id,
                depth=0,
            )

        return task

    async def move_task_status(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: TaskStatusMoveRequest,
    ) -> WorkItem:
        """
        Transition task to a new status and/or update its column rank.
        Validates configured workflow transitions when defined.
        """
        task = await self.get_task_by_id(task_id, project_id=project_id)

        # Optimistic concurrency check
        if request.version is not None and task.version != request.version:
            raise DomainException(
                code="STALE_OBJECT_ERROR",
                message=(
                    "The task has been modified by another transaction. Please reload."
                ),
                status_code=status.HTTP_409_CONFLICT,
                details={
                    "current_version": task.version,
                    "provided_version": request.version,
                },
            )

        await self.work_item_repo.ensure_default_workflow_statuses(project_id)

        target_status_obj = await self.work_item_repo.get_status_by_name_or_id(
            project_id, request.status
        )
        if target_status_obj is None:
            raise DomainException(
                code="INVALID_STATUS",
                message=f"Status '{request.status}' is not valid for this project.",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={"status": request.status},
            )

        if target_status_obj.name.lower() in ("to do", "todo"):
            new_status_name = "todo"
        elif target_status_obj.name.lower() in ("in progress", "in_progress"):
            new_status_name = "in_progress"
        elif target_status_obj.name.lower() in ("done",):
            new_status_name = "done"
        else:
            new_status_name = target_status_obj.name
        old_status = task.status

        # Validate dependency blocking if moving to completed status
        is_completing = new_status_name.lower() in COMPLETED_STATUSES or (
            target_status_obj.category
            and target_status_obj.category.lower() in COMPLETED_STATUSES
        )
        if is_completing and new_status_name != task.status:
            incomplete_preds = await self.work_item_repo.get_incomplete_predecessors(
                task.id
            )
            if incomplete_preds:
                pred_keys = [p.issue_key for p in incomplete_preds]
                raise DomainException(
                    code="TASK_BLOCKED_BY_DEPENDENCY",
                    message=(
                        f"Task '{task.issue_key}' cannot be completed because it has "
                        f"unfinished predecessor tasks: {', '.join(pred_keys)}."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={
                        "task_id": str(task.id),
                        "blocked_by": [str(p.id) for p in incomplete_preds],
                        "blocked_by_keys": pred_keys,
                    },
                )

        # Validate workflow transition policy if transitions are configured
        if new_status_name != task.status:
            current_status_obj = await self.work_item_repo.get_status_by_name_or_id(
                project_id, task.status
            )
            has_transitions = await self.work_item_repo.has_configured_transitions(
                project_id
            )

            if has_transitions and current_status_obj is not None:
                allowed_transitions = await self.work_item_repo.get_allowed_transitions(
                    current_status_obj.id
                )
                allowed_to_ids = {t.to_status_id for t in allowed_transitions}
                if target_status_obj.id not in allowed_to_ids:
                    raise DomainException(
                        code="INVALID_STATUS_TRANSITION",
                        message=(
                            f"Transition from '{task.status}' to '{request.status}' "
                            f"is not permitted by project workflow rules."
                        ),
                        status_code=status.HTTP_400_BAD_REQUEST,
                        details={
                            "from_status": task.status,
                            "to_status": request.status,
                        },
                    )

            # Record status transition history
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "status",
                task.status,
                new_status_name,
            )
            task.status = new_status_name
            if new_status_name.lower() in COMPLETED_STATUSES:
                task.completed_at = datetime.now(UTC)
            else:
                task.completed_at = None

            status_notifications: list[Notification] = []
            notified_users: set[uuid.UUID] = {actor_id}
            if task.assignee_id and task.assignee_id not in notified_users:
                notified_users.add(task.assignee_id)
                notif = await self.collab_repo.create_notification(
                    user_id=task.assignee_id,
                    actor_id=actor_id,
                    organization_id=task.organization_id,
                    type="task_status_changed",
                    title=f"Status Changed on {task.issue_key}",
                    message=f"Task {task.issue_key} was moved to {new_status_name}",
                    resource_type="task",
                    resource_id=task.id,
                )
                status_notifications.append(notif)

            if task.reporter_id and task.reporter_id not in notified_users:
                notified_users.add(task.reporter_id)
                notif = await self.collab_repo.create_notification(
                    user_id=task.reporter_id,
                    actor_id=actor_id,
                    organization_id=task.organization_id,
                    type="task_status_changed",
                    title=f"Status Changed on {task.issue_key}",
                    message=f"Task {task.issue_key} was moved to {new_status_name}",
                    resource_type="task",
                    resource_id=task.id,
                )
                status_notifications.append(notif)

        if request.rank is not None and request.rank != task.rank:
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "rank",
                task.rank,
                request.rank,
            )
            task.rank = request.rank

        task.version += 1
        await self.session.commit()
        await self.session.refresh(task)

        for status_notif in status_notifications:
            await self.broadcaster.broadcast_to_user(
                status_notif.user_id,
                "notification_created",
                {
                    "id": str(status_notif.id),
                    "user_id": str(status_notif.user_id),
                    "actor_id": (
                        str(status_notif.actor_id) if status_notif.actor_id else None
                    ),
                    "organization_id": (
                        str(status_notif.organization_id)
                        if status_notif.organization_id
                        else None
                    ),
                    "type": status_notif.type,
                    "title": status_notif.title,
                    "message": status_notif.message,
                    "resource_type": status_notif.resource_type,
                    "resource_id": (
                        str(status_notif.resource_id)
                        if status_notif.resource_id
                        else None
                    ),
                    "is_read": status_notif.is_read,
                    "created_at": status_notif.created_at.isoformat(),
                    "updated_at": status_notif.updated_at.isoformat(),
                },
            )

        if new_status_name != old_status:
            await self.execute_automations(
                project_id=project_id,
                trigger_event="task_status_changed",
                task=task,
                actor_id=actor_id,
                depth=0,
            )
            if task.status.lower() in COMPLETED_STATUSES:
                await self.execute_automations(
                    project_id=project_id,
                    trigger_event="task_completed",
                    task=task,
                    actor_id=actor_id,
                    depth=0,
                )

        return task

    async def delete_task(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        """Delete task and record activity."""
        task = await self.get_task_by_id(task_id, project_id=project_id)
        await self.work_item_repo.delete_work_item(task)
        await self.session.commit()

    async def get_kanban_board(
        self,
        project_id: uuid.UUID,
    ) -> KanbanBoardResponse:
        """
        Retrieve project tasks grouped by workflow status columns in order.
        """
        # Ensure default statuses exist if project has none
        statuses = await self.work_item_repo.ensure_default_workflow_statuses(
            project_id
        )
        tasks = await self.work_item_repo.list_all_project_work_items(project_id)

        # Group tasks by matching status name or category
        columns: list[KanbanColumn] = []
        task_dtos = [WorkItemResponse.model_validate(t) for t in tasks]

        for st in statuses:
            st_key = st.name.lower().replace(" ", "_")
            col_tasks = [
                t
                for t in task_dtos
                if t.status.lower().replace(" ", "_") == st_key
                or t.status.lower() == st.category.lower()
            ]
            columns.append(
                KanbanColumn(
                    status=WorkflowStatusResponse.model_validate(st),
                    tasks=col_tasks,
                    total_tasks=len(col_tasks),
                )
            )

        return KanbanBoardResponse(
            project_id=project_id,
            columns=columns,
            total_tasks=len(task_dtos),
        )

    async def get_task_history(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[WorkItemActivityResponse], int]:
        """Fetch paginated audit activity stream for a task."""
        await self.get_task_by_id(task_id, project_id=project_id)
        skip = (page - 1) * page_size
        activities, total = await self.work_item_repo.list_activities_for_task(
            work_item_id=task_id,
            skip=skip,
            limit=page_size,
        )

        results = []
        for act in activities:
            actor = None
            if act.user:
                actor = UserActivityActor(
                    id=act.user.id,
                    email=act.user.email,
                    full_name=act.user.full_name,
                )
            results.append(
                WorkItemActivityResponse(
                    id=act.id,
                    work_item_id=act.work_item_id,
                    user_id=act.user_id,
                    field=act.field,
                    old_value=act.old_value,
                    new_value=act.new_value,
                    created_at=act.created_at,
                    user=actor,
                )
            )

        return results, total

    # -------------------------------------------------------------
    # Task Details & Collaboration Aggregation
    # -------------------------------------------------------------

    async def get_task_details(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> WorkItemDetailResponse:
        """Fetch complete task details with nested subtasks, checklists, and counts."""
        task = await self.work_item_repo.get_task_details(
            task_id, project_id=project_id
        )
        if task is None:
            raise DomainException(
                code="TASK_NOT_FOUND",
                message="Task not found or inaccessible.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"task_id": str(task_id)},
            )

        project_brief = (
            ProjectBrief(
                id=task.project.id,
                name=task.project.name,
                key=task.project.key,
                organization_id=task.project.organization_id,
                is_private=task.project.is_private,
            )
            if task.project
            else None
        )

        reporter_dto = (
            UserActivityActor(
                id=task.reporter.id,
                email=task.reporter.email,
                full_name=task.reporter.full_name,
            )
            if task.reporter
            else None
        )

        assignee_dto = (
            UserActivityActor(
                id=task.assignee.id,
                email=task.assignee.email,
                full_name=task.assignee.full_name,
            )
            if task.assignee
            else None
        )

        # Subtasks
        subtasks_dtos = [
            WorkItemResponse.model_validate(c) for c in (task.children or [])
        ]

        # Checklists
        checklists_dtos = []
        completed_count = 0
        for chk in task.checklists or []:
            if chk.is_completed:
                completed_count += 1
            chk_assignee = (
                UserActivityActor(
                    id=chk.assignee.id,
                    email=chk.assignee.email,
                    full_name=chk.assignee.full_name,
                )
                if chk.assignee
                else None
            )
            checklists_dtos.append(
                ChecklistItemResponse(
                    id=chk.id,
                    work_item_id=chk.work_item_id,
                    title=chk.title,
                    is_completed=chk.is_completed,
                    position=chk.position,
                    assignee_id=chk.assignee_id,
                    assignee=chk_assignee,
                    created_at=chk.created_at,
                    updated_at=chk.updated_at,
                )
            )

        # Attachments
        attachments_dtos = []
        for att in task.attachments or []:
            att_uploader = (
                UserActivityActor(
                    id=att.uploader.id,
                    email=att.uploader.email,
                    full_name=att.uploader.full_name,
                )
                if att.uploader
                else None
            )
            attachments_dtos.append(
                AttachmentResponse(
                    id=att.id,
                    work_item_id=att.work_item_id,
                    uploader_id=att.uploader_id,
                    file_name=att.file_name,
                    file_size=att.file_size,
                    content_type=att.content_type,
                    storage_path=att.storage_path,
                    created_at=att.created_at,
                    uploader=att_uploader,
                )
            )

        comments_count = len(task.comments) if task.comments else 0

        milestone_dto = (
            MilestoneBrief.model_validate(task.milestone) if task.milestone else None
        )
        dependencies_dto = TaskDependenciesSummary(
            predecessors=[
                TaskDependencyResponse.model_validate(dep)
                for dep in (task.predecessor_dependencies or [])
            ],
            successors=[
                TaskDependencyResponse.model_validate(dep)
                for dep in (task.successor_dependencies or [])
            ],
        )

        return WorkItemDetailResponse(
            id=task.id,
            project_id=task.project_id,
            organization_id=task.organization_id,
            sequence_number=task.sequence_number,
            issue_key=task.issue_key,
            type=task.type,
            title=task.title,
            description=task.description,
            status=task.status,
            priority=task.priority,
            parent_id=task.parent_id,
            reporter_id=task.reporter_id,
            assignee_id=task.assignee_id,
            sprint_id=task.sprint_id,
            rank=task.rank,
            start_date=task.start_date,
            due_date=task.due_date,
            milestone_id=task.milestone_id,
            estimated_points=task.estimated_points,
            version=task.version,
            created_at=task.created_at,
            updated_at=task.updated_at,
            project=project_brief,
            reporter=reporter_dto,
            assignee=assignee_dto,
            milestone=milestone_dto,
            dependencies=dependencies_dto,
            subtasks=subtasks_dtos,
            checklists=checklists_dtos,
            attachments=attachments_dtos,
            subtasks_count=len(subtasks_dtos),
            checklists_count=len(checklists_dtos),
            completed_checklists_count=completed_count,
            attachments_count=len(attachments_dtos),
            comments_count=comments_count,
        )

    # -------------------------------------------------------------
    # Checklists
    # -------------------------------------------------------------

    async def create_checklist_item(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: ChecklistItemCreateRequest,
    ) -> ChecklistItemResponse:
        """Create a checklist item on a task."""
        task = await self.get_task_by_id(task_id, project_id=project_id)
        if request.assignee_id is not None:
            mem = await self.org_repo.get_membership(
                task.organization_id, request.assignee_id
            )
            if mem is None or not mem.is_active:
                raise DomainException(
                    code="INVALID_ASSIGNEE",
                    message=(
                        "Checklist assignee must be an active member of the "
                        "organization."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"assignee_id": str(request.assignee_id)},
                )

        item = await self.work_item_repo.create_checklist_item(
            work_item_id=task.id,
            title=request.title.strip(),
            assignee_id=request.assignee_id,
            position=request.position,
        )

        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=actor_id,
            field="checklist_item_added",
            old_value=None,
            new_value=f"Added checklist item: {item.title}",
        )

        await self.session.commit()
        reloaded = await self.work_item_repo.get_checklist_item_by_id(item.id)
        assert reloaded is not None
        assignee_dto = (
            UserActivityActor(
                id=reloaded.assignee.id,
                email=reloaded.assignee.email,
                full_name=reloaded.assignee.full_name,
            )
            if reloaded.assignee
            else None
        )
        return ChecklistItemResponse(
            id=reloaded.id,
            work_item_id=reloaded.work_item_id,
            title=reloaded.title,
            is_completed=reloaded.is_completed,
            position=reloaded.position,
            assignee_id=reloaded.assignee_id,
            assignee=assignee_dto,
            created_at=reloaded.created_at,
            updated_at=reloaded.updated_at,
        )

    async def list_checklist_items(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> ChecklistListResponse:
        """List all checklist items under a task in position order."""
        await self.get_task_by_id(task_id, project_id=project_id)
        items = await self.work_item_repo.list_checklist_items(task_id)
        dtos = []
        for i in items:
            assignee_dto = (
                UserActivityActor(
                    id=i.assignee.id,
                    email=i.assignee.email,
                    full_name=i.assignee.full_name,
                )
                if i.assignee
                else None
            )
            dtos.append(
                ChecklistItemResponse(
                    id=i.id,
                    work_item_id=i.work_item_id,
                    title=i.title,
                    is_completed=i.is_completed,
                    position=i.position,
                    assignee_id=i.assignee_id,
                    assignee=assignee_dto,
                    created_at=i.created_at,
                    updated_at=i.updated_at,
                )
            )
        return ChecklistListResponse(items=dtos, total=len(dtos))

    async def update_checklist_item(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        item_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: ChecklistItemUpdateRequest,
    ) -> ChecklistItemResponse:
        """Update checklist item content, completion, position, or assignee."""
        task = await self.get_task_by_id(task_id, project_id=project_id)
        item = await self.work_item_repo.get_checklist_item_by_id(
            item_id, work_item_id=task.id
        )
        if item is None:
            raise DomainException(
                code="CHECKLIST_ITEM_NOT_FOUND",
                message="Checklist item not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if request.assignee_id is not None and request.assignee_id != item.assignee_id:
            mem = await self.org_repo.get_membership(
                task.organization_id, request.assignee_id
            )
            if mem is None or not mem.is_active:
                raise DomainException(
                    code="INVALID_ASSIGNEE",
                    message=(
                        "Checklist assignee must be an active member of the "
                        "organization."
                    ),
                    status_code=status.HTTP_400_BAD_REQUEST,
                    details={"assignee_id": str(request.assignee_id)},
                )
            item.assignee_id = request.assignee_id

        if request.title is not None and request.title.strip() != item.title:
            item.title = request.title.strip()

        if request.position is not None:
            item.position = request.position

        if (
            request.is_completed is not None
            and request.is_completed != item.is_completed
        ):
            item.is_completed = request.is_completed
            field_name = (
                "checklist_item_completed"
                if item.is_completed
                else "checklist_item_uncompleted"
            )
            await self.work_item_repo.record_activity(
                work_item_id=task.id,
                user_id=actor_id,
                field=field_name,
                old_value=item.title,
                new_value="completed" if item.is_completed else "incomplete",
            )

        await self.session.commit()
        reloaded = await self.work_item_repo.get_checklist_item_by_id(item.id)
        assert reloaded is not None
        assignee_dto = (
            UserActivityActor(
                id=reloaded.assignee.id,
                email=reloaded.assignee.email,
                full_name=reloaded.assignee.full_name,
            )
            if reloaded.assignee
            else None
        )
        return ChecklistItemResponse(
            id=reloaded.id,
            work_item_id=reloaded.work_item_id,
            title=reloaded.title,
            is_completed=reloaded.is_completed,
            position=reloaded.position,
            assignee_id=reloaded.assignee_id,
            assignee=assignee_dto,
            created_at=reloaded.created_at,
            updated_at=reloaded.updated_at,
        )

    async def delete_checklist_item(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        item_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        """Delete checklist item and record activity."""
        task = await self.get_task_by_id(task_id, project_id=project_id)
        item = await self.work_item_repo.get_checklist_item_by_id(
            item_id, work_item_id=task.id
        )
        if item is None:
            raise DomainException(
                code="CHECKLIST_ITEM_NOT_FOUND",
                message="Checklist item not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=actor_id,
            field="checklist_item_deleted",
            old_value=item.title,
            new_value=None,
        )
        await self.work_item_repo.delete_checklist_item(item)
        await self.session.commit()

    # -------------------------------------------------------------
    # Attachments
    # -------------------------------------------------------------

    async def upload_attachment(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
        file: UploadFile,
    ) -> AttachmentResponse:
        """Upload and store file attachment, persisting metadata."""
        task = await self.get_task_by_id(task_id, project_id=project_id)
        safe_name, rel_path, file_size, content_type = await default_storage.save_file(
            file=file,
            subfolder=str(task.id),
        )

        attachment = await self.work_item_repo.create_attachment(
            work_item_id=task.id,
            uploader_id=actor_id,
            file_name=safe_name,
            file_size=file_size,
            content_type=content_type,
            storage_path=rel_path,
        )

        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=actor_id,
            field="attachment_uploaded",
            old_value=None,
            new_value=f"Attached file: {safe_name} ({file_size} bytes)",
        )

        await self.session.commit()
        reloaded = await self.work_item_repo.get_attachment_by_id(attachment.id)
        assert reloaded is not None
        uploader_dto = (
            UserActivityActor(
                id=reloaded.uploader.id,
                email=reloaded.uploader.email,
                full_name=reloaded.uploader.full_name,
            )
            if reloaded.uploader
            else None
        )
        return AttachmentResponse(
            id=reloaded.id,
            work_item_id=reloaded.work_item_id,
            uploader_id=reloaded.uploader_id,
            file_name=reloaded.file_name,
            file_size=reloaded.file_size,
            content_type=reloaded.content_type,
            storage_path=reloaded.storage_path,
            created_at=reloaded.created_at,
            uploader=uploader_dto,
        )

    async def list_attachments(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> AttachmentListResponse:
        """List all attachments associated with a task."""
        await self.get_task_by_id(task_id, project_id=project_id)
        attachments = await self.work_item_repo.list_attachments(task_id)
        dtos = []
        for att in attachments:
            uploader_dto = (
                UserActivityActor(
                    id=att.uploader.id,
                    email=att.uploader.email,
                    full_name=att.uploader.full_name,
                )
                if att.uploader
                else None
            )
            dtos.append(
                AttachmentResponse(
                    id=att.id,
                    work_item_id=att.work_item_id,
                    uploader_id=att.uploader_id,
                    file_name=att.file_name,
                    file_size=att.file_size,
                    content_type=att.content_type,
                    storage_path=att.storage_path,
                    created_at=att.created_at,
                    uploader=uploader_dto,
                )
            )
        return AttachmentListResponse(items=dtos, total=len(dtos))

    async def get_attachment(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        attachment_id: uuid.UUID,
    ) -> AttachmentResponse:
        """Fetch attachment metadata."""
        await self.get_task_by_id(task_id, project_id=project_id)
        att = await self.work_item_repo.get_attachment_by_id(
            attachment_id, work_item_id=task_id
        )
        if att is None:
            raise DomainException(
                code="ATTACHMENT_NOT_FOUND",
                message="Attachment not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        uploader_dto = (
            UserActivityActor(
                id=att.uploader.id,
                email=att.uploader.email,
                full_name=att.uploader.full_name,
            )
            if att.uploader
            else None
        )
        return AttachmentResponse(
            id=att.id,
            work_item_id=att.work_item_id,
            uploader_id=att.uploader_id,
            file_name=att.file_name,
            file_size=att.file_size,
            content_type=att.content_type,
            storage_path=att.storage_path,
            created_at=att.created_at,
            uploader=uploader_dto,
        )

    async def get_attachment_file(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        attachment_id: uuid.UUID,
    ) -> tuple[Path, Attachment]:
        """Fetch attachment file path for downloading."""
        await self.get_task_by_id(task_id, project_id=project_id)
        att = await self.work_item_repo.get_attachment_by_id(
            attachment_id, work_item_id=task_id
        )
        if att is None:
            raise DomainException(
                code="ATTACHMENT_NOT_FOUND",
                message="Attachment not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        file_path = default_storage.get_absolute_path(att.storage_path)
        if not file_path.exists() or not file_path.is_file():
            raise DomainException(
                code="ATTACHMENT_FILE_MISSING",
                message="Attachment file is missing from storage.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return file_path, att

    async def delete_attachment(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        attachment_id: uuid.UUID,
        actor_id: uuid.UUID,
        is_admin_or_lead: bool,
    ) -> None:
        """Delete attachment file and database record."""
        task = await self.get_task_by_id(task_id, project_id=project_id)
        att = await self.work_item_repo.get_attachment_by_id(
            attachment_id, work_item_id=task.id
        )
        if att is None:
            raise DomainException(
                code="ATTACHMENT_NOT_FOUND",
                message="Attachment not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if att.uploader_id != actor_id and not is_admin_or_lead:
            raise DomainException(
                code="FORBIDDEN",
                message="You do not have permission to delete this attachment.",
                status_code=status.HTTP_403_FORBIDDEN,
            )

        await default_storage.delete_file(att.storage_path)
        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=actor_id,
            field="attachment_deleted",
            old_value=att.file_name,
            new_value=None,
        )
        await self.work_item_repo.delete_attachment(att)
        await self.session.commit()

    # -------------------------------------------------------------
    # Subtasks
    # -------------------------------------------------------------

    async def create_subtask(
        self,
        parent_task_id: uuid.UUID,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        creator_id: uuid.UUID,
        request: SubtaskCreateRequest,
    ) -> WorkItemResponse:
        """Create a child subtask under a parent work item."""
        parent = await self.get_task_by_id(parent_task_id, project_id=project_id)
        create_req = WorkItemCreateRequest(
            title=request.title,
            description=request.description,
            type="subtask",
            priority=request.priority,
            status=request.status,
            assignee_id=request.assignee_id,
            parent_id=parent.id,
            due_date=request.due_date,
            estimated_points=request.estimated_points,
        )
        subtask = await self.create_task(
            project_id=project_id,
            org_id=org_id,
            creator_id=creator_id,
            request=create_req,
        )
        await self.work_item_repo.record_activity(
            work_item_id=parent.id,
            user_id=creator_id,
            field="subtask_created",
            old_value=None,
            new_value=f"Created subtask {subtask.issue_key}: {subtask.title}",
        )
        await self.session.commit()
        return WorkItemResponse.model_validate(subtask)

    async def list_subtasks(
        self,
        task_id: uuid.UUID,
        project_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> WorkItemListResponse:
        """List child subtasks of a parent task."""
        await self.get_task_by_id(task_id, project_id=project_id)
        skip = (page - 1) * page_size
        subtasks, total = await self.work_item_repo.list_subtasks(
            parent_id=task_id,
            skip=skip,
            limit=page_size,
        )
        return WorkItemListResponse(
            items=[WorkItemResponse.model_validate(s) for s in subtasks],
            page=page,
            page_size=page_size,
            total=total,
        )

    # -------------------------------------------------------------
    # Phase 8: Milestones
    # -------------------------------------------------------------

    async def create_milestone(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: MilestoneCreateRequest,
    ) -> MilestoneResponse:
        """Create a new project milestone."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        existing = await self.work_item_repo.get_milestone_by_name(
            project_id, request.name
        )
        if existing is not None:
            raise DomainException(
                code="DUPLICATE_MILESTONE",
                message=f"Milestone '{request.name}' already exists in this project.",
                status_code=status.HTTP_409_CONFLICT,
            )

        milestone = await self.work_item_repo.create_milestone(
            project_id=project_id,
            name=request.name.strip(),
            description=request.description.strip() if request.description else None,
            target_date=request.target_date,
            is_completed=request.is_completed,
        )
        await self.session.commit()
        await self.session.refresh(milestone)

        return MilestoneResponse(
            id=milestone.id,
            project_id=milestone.project_id,
            name=milestone.name,
            description=milestone.description,
            target_date=milestone.target_date,
            is_completed=milestone.is_completed,
            total_tasks=0,
            completed_tasks=0,
            created_at=milestone.created_at,
            updated_at=milestone.updated_at,
        )

    async def get_milestone(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        milestone_id: uuid.UUID,
    ) -> MilestoneResponse:
        """Fetch milestone details with task statistics."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        milestone = await self.work_item_repo.get_milestone_by_id(
            milestone_id, project_id=project_id
        )
        if milestone is None:
            raise DomainException(
                code="MILESTONE_NOT_FOUND",
                message="Milestone not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        counts = await self.work_item_repo.get_milestone_task_counts(project_id)
        total, completed = counts.get(milestone.id, (0, 0))

        return MilestoneResponse(
            id=milestone.id,
            project_id=milestone.project_id,
            name=milestone.name,
            description=milestone.description,
            target_date=milestone.target_date,
            is_completed=milestone.is_completed,
            total_tasks=total,
            completed_tasks=completed,
            created_at=milestone.created_at,
            updated_at=milestone.updated_at,
        )

    async def list_milestones(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        is_completed: bool | None = None,
    ) -> MilestoneListResponse:
        """List all milestones in project with completion statistics."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        milestones = await self.work_item_repo.list_milestones(
            project_id, is_completed=is_completed
        )
        counts = await self.work_item_repo.get_milestone_task_counts(project_id)

        items = [
            MilestoneResponse(
                id=m.id,
                project_id=m.project_id,
                name=m.name,
                description=m.description,
                target_date=m.target_date,
                is_completed=m.is_completed,
                total_tasks=counts.get(m.id, (0, 0))[0],
                completed_tasks=counts.get(m.id, (0, 0))[1],
                created_at=m.created_at,
                updated_at=m.updated_at,
            )
            for m in milestones
        ]

        return MilestoneListResponse(items=items, total=len(items))

    async def update_milestone(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        milestone_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: MilestoneUpdateRequest,
    ) -> MilestoneResponse:
        """Update milestone properties."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        milestone = await self.work_item_repo.get_milestone_by_id(
            milestone_id, project_id=project_id
        )
        if milestone is None:
            raise DomainException(
                code="MILESTONE_NOT_FOUND",
                message="Milestone not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        if request.name is not None and request.name.strip() != milestone.name:
            existing = await self.work_item_repo.get_milestone_by_name(
                project_id, request.name
            )
            if existing is not None and existing.id != milestone.id:
                raise DomainException(
                    code="DUPLICATE_MILESTONE",
                    message=(
                        f"Milestone '{request.name}' already exists in this project."
                    ),
                    status_code=status.HTTP_409_CONFLICT,
                )
            milestone.name = request.name.strip()

        if request.description is not None:
            milestone.description = (
                request.description.strip() if request.description else None
            )

        if request.target_date is not None:
            milestone.target_date = request.target_date

        if request.is_completed is not None:
            milestone.is_completed = request.is_completed

        milestone.updated_at = datetime.now(UTC)
        await self.session.commit()
        await self.session.refresh(milestone)

        counts = await self.work_item_repo.get_milestone_task_counts(project_id)
        total, completed = counts.get(milestone.id, (0, 0))

        return MilestoneResponse(
            id=milestone.id,
            project_id=milestone.project_id,
            name=milestone.name,
            description=milestone.description,
            target_date=milestone.target_date,
            is_completed=milestone.is_completed,
            total_tasks=total,
            completed_tasks=completed,
            created_at=milestone.created_at,
            updated_at=milestone.updated_at,
        )

    async def delete_milestone(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        milestone_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        """Delete milestone and clear references from associated work items."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        milestone = await self.work_item_repo.get_milestone_by_id(
            milestone_id, project_id=project_id
        )
        if milestone is None:
            raise DomainException(
                code="MILESTONE_NOT_FOUND",
                message="Milestone not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        await self.work_item_repo.delete_milestone(milestone)
        await self.session.commit()

    # -------------------------------------------------------------
    # Phase 8: Task Dependencies
    # -------------------------------------------------------------

    async def create_dependency(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: TaskDependencyCreateRequest,
    ) -> TaskDependencyResponse:
        """Create task dependency with strict cross-project and DAG cycle validation."""
        if request.predecessor_id == request.successor_id:
            raise DomainException(
                code="SELF_DEPENDENCY",
                message="A task cannot depend on itself.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        pred_task = await self.work_item_repo.get_by_id(request.predecessor_id)
        if (
            pred_task is None
            or pred_task.project_id != project_id
            or pred_task.organization_id != org_id
        ):
            raise DomainException(
                code="PREDECESSOR_TASK_NOT_FOUND",
                message="Predecessor task not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"predecessor_id": str(request.predecessor_id)},
            )

        succ_task = await self.work_item_repo.get_by_id(request.successor_id)
        if (
            succ_task is None
            or succ_task.project_id != project_id
            or succ_task.organization_id != org_id
        ):
            raise DomainException(
                code="SUCCESSOR_TASK_NOT_FOUND",
                message="Successor task not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"successor_id": str(request.successor_id)},
            )

        # Check for duplicate dependency
        existing_dep = await self.work_item_repo.get_dependency_by_nodes(
            request.predecessor_id, request.successor_id
        )
        if existing_dep is not None:
            raise DomainException(
                code="DUPLICATE_DEPENDENCY",
                message="This dependency relationship already exists.",
                status_code=status.HTTP_409_CONFLICT,
            )

        # Check for circular dependency
        is_circular = await self.work_item_repo.check_circular_dependency(
            project_id=project_id,
            predecessor_id=request.predecessor_id,
            successor_id=request.successor_id,
        )
        if is_circular:
            raise DomainException(
                code="CIRCULAR_DEPENDENCY",
                message=(
                    f"Adding dependency from {pred_task.issue_key} to "
                    f"{succ_task.issue_key} would create a circular dependency cycle."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "predecessor_id": str(request.predecessor_id),
                    "successor_id": str(request.successor_id),
                },
            )

        dep = await self.work_item_repo.create_dependency(
            project_id=project_id,
            predecessor_id=request.predecessor_id,
            successor_id=request.successor_id,
            dependency_type=request.dependency_type,
        )

        await self.work_item_repo.record_activity(
            work_item_id=request.successor_id,
            user_id=actor_id,
            field="dependency_added",
            old_value=None,
            new_value=f"Depends on {pred_task.issue_key}",
        )
        await self.session.commit()
        await self.session.refresh(dep)

        return TaskDependencyResponse.model_validate(dep)

    async def list_dependencies_for_project(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> TaskDependencyListResponse:
        """List all task dependencies in a project."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        deps = await self.work_item_repo.list_dependencies_for_project(project_id)
        dtos = [TaskDependencyResponse.model_validate(d) for d in deps]
        return TaskDependencyListResponse(items=dtos, total=len(dtos))

    async def list_dependencies_for_task(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
    ) -> TaskDependenciesSummary:
        """List direct incoming and outgoing dependencies for a specific task."""
        task = await self.get_task_by_id(task_id, project_id=project_id)
        preds, succs = await self.work_item_repo.list_dependencies_for_task(task.id)
        return TaskDependenciesSummary(
            predecessors=[TaskDependencyResponse.model_validate(d) for d in preds],
            successors=[TaskDependencyResponse.model_validate(d) for d in succs],
        )

    async def delete_dependency(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        dependency_id: uuid.UUID,
        actor_id: uuid.UUID,
    ) -> None:
        """Remove a task dependency relationship."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        dep = await self.work_item_repo.get_dependency_by_id(
            dependency_id, project_id=project_id
        )
        if dep is None:
            raise DomainException(
                code="DEPENDENCY_NOT_FOUND",
                message="Task dependency not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        pred_id = dep.predecessor_id
        succ_id = dep.successor_id

        await self.work_item_repo.delete_dependency(dep)
        await self.work_item_repo.record_activity(
            work_item_id=succ_id,
            user_id=actor_id,
            field="dependency_removed",
            old_value=str(pred_id),
            new_value=None,
        )
        await self.session.commit()

    # -------------------------------------------------------------
    # Phase 8: Calendar & Timeline Views
    # -------------------------------------------------------------

    async def get_calendar_view(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        start_date: datetime,
        end_date: datetime,
        assignee_id: uuid.UUID | None = None,
        task_status: str | None = None,
        include_unscheduled: bool = False,
    ) -> CalendarResponse:
        """Retrieve authorized project tasks spanning calendar date window."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        norm_start = _ensure_utc(start_date)
        norm_end = _ensure_utc(end_date)
        if norm_start is None or norm_end is None or norm_end < norm_start:
            raise DomainException(
                code="INVALID_DATE_RANGE",
                message="end_date must be greater than or equal to start_date.",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "start_date": start_date.isoformat(),
                    "end_date": end_date.isoformat(),
                },
            )

        (
            scheduled_tasks,
            unscheduled_tasks,
        ) = await self.work_item_repo.get_calendar_tasks(
            project_id=project_id,
            start_date=norm_start,
            end_date=norm_end,
            assignee_id=assignee_id,
            status=task_status,
            include_unscheduled=include_unscheduled,
        )

        def _map_calendar_item(task: WorkItem) -> CalendarTaskItem:
            assignee_dto = (
                UserActivityActor(
                    id=task.assignee.id,
                    email=task.assignee.email,
                    full_name=task.assignee.full_name,
                )
                if task.assignee
                else None
            )
            return CalendarTaskItem(
                id=task.id,
                issue_key=task.issue_key,
                type=task.type,
                title=task.title,
                status=task.status,
                priority=task.priority,
                start_date=task.start_date,
                due_date=task.due_date,
                estimated_points=task.estimated_points,
                assignee=assignee_dto,
                milestone_id=task.milestone_id,
                milestone_name=task.milestone.name if task.milestone else None,
            )

        scheduled_dtos = [_map_calendar_item(t) for t in scheduled_tasks]
        unscheduled_dtos = [_map_calendar_item(t) for t in unscheduled_tasks]

        return CalendarResponse(
            items=scheduled_dtos,
            unscheduled_items=unscheduled_dtos,
            total=len(scheduled_dtos),
            start_date=norm_start,
            end_date=norm_end,
        )

    async def get_timeline_view(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        include_unscheduled: bool = True,
    ) -> TimelineResponse:
        """Batch retrieve tasks, milestones, and dependencies for Timeline/Gantt."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        norm_start = _ensure_utc(start_date)
        norm_end = _ensure_utc(end_date)
        if norm_start is not None and norm_end is not None and norm_end < norm_start:
            raise DomainException(
                code="INVALID_DATE_RANGE",
                message="end_date must be greater than or equal to start_date.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        tasks, milestones, dependencies = await self.work_item_repo.get_timeline_data(
            project_id=project_id,
            start_date=start_date,
            end_date=end_date,
            include_unscheduled=include_unscheduled,
        )

        project_brief = ProjectBrief(
            id=project.id,
            name=project.name,
            key=project.key,
            organization_id=project.organization_id,
            is_private=project.is_private,
        )

        task_dtos = []
        for t in tasks:
            assignee_dto = (
                UserActivityActor(
                    id=t.assignee.id,
                    email=t.assignee.email,
                    full_name=t.assignee.full_name,
                )
                if t.assignee
                else None
            )
            has_dates = t.start_date is not None or t.due_date is not None
            task_dtos.append(
                TimelineTaskItem(
                    id=t.id,
                    issue_key=t.issue_key,
                    type=t.type,
                    title=t.title,
                    status=t.status,
                    priority=t.priority,
                    start_date=t.start_date,
                    due_date=t.due_date,
                    estimated_points=t.estimated_points,
                    rank=t.rank,
                    assignee=assignee_dto,
                    milestone_id=t.milestone_id,
                    milestone_name=t.milestone.name if t.milestone else None,
                    has_dates=has_dates,
                )
            )

        milestone_dtos = [
            TimelineMilestoneItem(
                id=m.id,
                name=m.name,
                description=m.description,
                target_date=m.target_date,
                is_completed=m.is_completed,
            )
            for m in milestones
        ]

        dependency_dtos = [
            TimelineDependencyItem(
                id=d.id,
                predecessor_id=d.predecessor_id,
                successor_id=d.successor_id,
                dependency_type=d.dependency_type,
            )
            for d in dependencies
        ]

        return TimelineResponse(
            project=project_brief,
            tasks=task_dtos,
            milestones=milestone_dtos,
            dependencies=dependency_dtos,
            start_date=start_date,
            end_date=end_date,
        )

    # ---------------- Phase 11: Workflow Policies ---------------- #

    async def create_workflow_status(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        request: WorkflowStatusCreateRequest,
    ) -> WorkflowStatusDetailResponse:
        """Create a custom workflow status for a project."""
        await self.get_project_or_404(project_id, org_id)
        status_obj = await self.work_item_repo.create_workflow_status(
            project_id=project_id,
            name=request.name.strip(),
            category=request.category,
            position=request.position,
            is_default=request.is_default,
        )
        await self.session.commit()
        await self.session.refresh(status_obj)
        return WorkflowStatusDetailResponse(
            id=status_obj.id,
            project_id=status_obj.project_id,
            name=status_obj.name,
            category=status_obj.category,
            position=status_obj.position,
            is_default=status_obj.is_default,
            created_at=status_obj.created_at,
            updated_at=status_obj.updated_at,
            allowed_transitions=[],
        )

    async def list_workflow_statuses(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> list[WorkflowStatusDetailResponse]:
        """List all workflow statuses with permitted transitions for a project."""
        await self.get_project_or_404(project_id, org_id)
        await self.work_item_repo.ensure_default_workflow_statuses(project_id)
        statuses = await self.work_item_repo.get_project_statuses(project_id)
        transitions = await self.work_item_repo.list_transitions_for_project(project_id)

        transitions_by_from: dict[uuid.UUID, list[WorkflowTransitionResponse]] = {}
        for t in transitions:
            dto = WorkflowTransitionResponse.model_validate(t)
            transitions_by_from.setdefault(t.from_status_id, []).append(dto)

        result: list[WorkflowStatusDetailResponse] = []
        for s in statuses:
            result.append(
                WorkflowStatusDetailResponse(
                    id=s.id,
                    project_id=s.project_id,
                    name=s.name,
                    category=s.category,
                    position=s.position,
                    is_default=s.is_default,
                    created_at=s.created_at,
                    updated_at=s.updated_at,
                    allowed_transitions=transitions_by_from.get(s.id, []),
                )
            )
        return result

    async def update_workflow_status(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        status_id: uuid.UUID,
        request: WorkflowStatusUpdateRequest,
    ) -> WorkflowStatusDetailResponse:
        """Update properties of an existing custom workflow status."""
        await self.get_project_or_404(project_id, org_id)
        status_obj = await self.work_item_repo.get_workflow_status_by_id(
            status_id, project_id=project_id
        )
        if status_obj is None or status_obj.project_id != project_id:
            raise DomainException(
                code="STATUS_NOT_FOUND",
                message="Workflow status not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"status_id": str(status_id)},
            )

        kwargs: dict[str, Any] = {}
        if request.name is not None:
            kwargs["name"] = request.name.strip()
        if request.category is not None:
            kwargs["category"] = request.category
        if request.position is not None:
            kwargs["position"] = request.position
        if request.is_default is not None:
            kwargs["is_default"] = request.is_default

        updated = await self.work_item_repo.update_workflow_status(status_obj, **kwargs)
        await self.session.commit()
        await self.session.refresh(updated)

        transitions = await self.work_item_repo.get_allowed_transitions(updated.id)
        return WorkflowStatusDetailResponse(
            id=updated.id,
            project_id=updated.project_id,
            name=updated.name,
            category=updated.category,
            position=updated.position,
            is_default=updated.is_default,
            created_at=updated.created_at,
            updated_at=updated.updated_at,
            allowed_transitions=[
                WorkflowTransitionResponse.model_validate(t) for t in transitions
            ],
        )

    async def delete_workflow_status(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        status_id: uuid.UUID,
    ) -> None:
        """Delete a custom workflow status if not in active use."""
        await self.get_project_or_404(project_id, org_id)
        status_obj = await self.work_item_repo.get_workflow_status_by_id(
            status_id, project_id=project_id
        )
        if status_obj is None or status_obj.project_id != project_id:
            raise DomainException(
                code="STATUS_NOT_FOUND",
                message="Workflow status not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"status_id": str(status_id)},
            )

        if status_obj.is_default:
            raise DomainException(
                code="CANNOT_DELETE_DEFAULT_STATUS",
                message="Cannot delete the default workflow status for a project.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        task_count = await self.work_item_repo.count_tasks_in_status(
            project_id, status_obj.name
        )
        if task_count > 0:
            raise DomainException(
                code="STATUS_IN_USE",
                message=(
                    f"Cannot delete status '{status_obj.name}' because it contains "
                    f"{task_count} active tasks. Please reassign tasks first."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
                details={"task_count": task_count},
            )

        await self.work_item_repo.delete_workflow_status(status_obj)
        await self.session.commit()

    async def create_workflow_transition(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        request: WorkflowTransitionCreateRequest,
    ) -> WorkflowTransitionResponse:
        """Define an explicit permitted transition between workflow statuses."""
        await self.get_project_or_404(project_id, org_id)
        from_st = await self.work_item_repo.get_workflow_status_by_id(
            request.from_status_id, project_id=project_id
        )
        to_st = await self.work_item_repo.get_workflow_status_by_id(
            request.to_status_id, project_id=project_id
        )
        if from_st is None or to_st is None:
            raise DomainException(
                code="STATUS_NOT_FOUND",
                message="Source or destination status not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        existing = await self.work_item_repo.get_transition_by_from_to(
            request.from_status_id, request.to_status_id
        )
        if existing is not None:
            raise DomainException(
                code="TRANSITION_ALREADY_EXISTS",
                message="This status transition rule is already configured.",
                status_code=status.HTTP_409_CONFLICT,
            )

        trans = await self.work_item_repo.create_workflow_transition(
            from_status_id=request.from_status_id,
            to_status_id=request.to_status_id,
            name=request.name.strip() if request.name else None,
        )
        await self.session.commit()
        await self.session.refresh(trans)
        return WorkflowTransitionResponse.model_validate(trans)

    async def list_workflow_transitions(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> list[WorkflowTransitionResponse]:
        """List all configured status transitions for a project."""
        await self.get_project_or_404(project_id, org_id)
        transitions = await self.work_item_repo.list_transitions_for_project(project_id)
        return [WorkflowTransitionResponse.model_validate(t) for t in transitions]

    async def delete_workflow_transition(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        transition_id: uuid.UUID,
    ) -> None:
        """Remove a configured workflow transition rule."""
        await self.get_project_or_404(project_id, org_id)
        trans = await self.work_item_repo.get_workflow_transition_by_id(transition_id)
        if trans is None:
            raise DomainException(
                code="TRANSITION_NOT_FOUND",
                message="Workflow transition rule not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"transition_id": str(transition_id)},
            )
        await self.work_item_repo.delete_workflow_transition(trans)
        await self.session.commit()

    # ---------------- Phase 11: Task Templates ---------------- #

    async def create_template(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        creator_id: uuid.UUID,
        request: TaskTemplateCreateRequest,
    ) -> TaskTemplate:
        """Create a reusable task blueprint in a project."""
        await self.get_project_or_404(project_id, org_id)
        existing = await self.work_item_repo.get_template_by_name(
            project_id, request.name
        )
        if existing is not None:
            raise DomainException(
                code="TEMPLATE_NAME_EXISTS",
                message=(
                    f"Template with name '{request.name}' already exists in project."
                ),
                status_code=status.HTTP_409_CONFLICT,
                details={"name": request.name},
            )

        template = await self.work_item_repo.create_template(
            project_id=project_id,
            organization_id=org_id,
            name=request.name.strip(),
            task_title=request.task_title.strip(),
            description=request.description.strip() if request.description else None,
            task_description=(
                request.task_description.strip() if request.task_description else None
            ),
            task_type=request.task_type,
            priority=request.priority,
            estimated_points=request.estimated_points,
            checklist_definitions=request.checklist_definitions,
            default_assignee_id=request.default_assignee_id,
            created_by_id=creator_id,
        )
        await self.session.commit()
        await self.session.refresh(template)
        return template

    async def get_template(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        template_id: uuid.UUID,
    ) -> TaskTemplate:
        """Get a specific task template by ID."""
        await self.get_project_or_404(project_id, org_id)
        template = await self.work_item_repo.get_template(
            template_id, project_id=project_id
        )
        if template is None:
            raise DomainException(
                code="TEMPLATE_NOT_FOUND",
                message="Task template not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"template_id": str(template_id)},
            )
        return template

    async def list_templates(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> list[TaskTemplate]:
        """List all task templates defined in a project."""
        await self.get_project_or_404(project_id, org_id)
        return await self.work_item_repo.list_templates(project_id)

    async def update_template(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        template_id: uuid.UUID,
        request: TaskTemplateUpdateRequest,
    ) -> TaskTemplate:
        """Update blueprint attributes of an existing task template."""
        template = await self.get_template(project_id, org_id, template_id)

        kwargs: dict[str, Any] = {}
        if request.name is not None and request.name.strip() != template.name:
            dup = await self.work_item_repo.get_template_by_name(
                project_id, request.name
            )
            if dup is not None and dup.id != template.id:
                raise DomainException(
                    code="TEMPLATE_NAME_EXISTS",
                    message=(f"Template with name '{request.name}' already exists."),
                    status_code=status.HTTP_409_CONFLICT,
                )
            kwargs["name"] = request.name.strip()

        if request.description is not None:
            kwargs["description"] = (
                request.description.strip() if request.description else None
            )
        if request.task_title is not None:
            kwargs["task_title"] = request.task_title.strip()
        if request.task_description is not None:
            kwargs["task_description"] = (
                request.task_description.strip() if request.task_description else None
            )
        if request.task_type is not None:
            kwargs["task_type"] = request.task_type
        if request.priority is not None:
            kwargs["priority"] = request.priority
        if request.estimated_points is not None:
            kwargs["estimated_points"] = request.estimated_points
        if request.checklist_definitions is not None:
            kwargs["checklist_definitions"] = request.checklist_definitions
        if "default_assignee_id" in request.model_fields_set:
            kwargs["default_assignee_id"] = request.default_assignee_id

        updated = await self.work_item_repo.update_template(template, **kwargs)
        await self.session.commit()
        await self.session.refresh(updated)
        return updated

    async def delete_template(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        template_id: uuid.UUID,
    ) -> None:
        """Delete a task template from a project."""
        template = await self.get_template(project_id, org_id, template_id)
        await self.work_item_repo.delete_template(template)
        await self.session.commit()

    async def instantiate_template(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
        template_id: uuid.UUID,
        request: InstantiateTemplateRequest,
    ) -> WorkItem:
        """
        Instantiate a new task from a template blueprint.
        Copies core fields and checklist items, without copying IDs or comments.
        """
        template = await self.get_template(project_id, org_id, template_id)

        task_create_req = WorkItemCreateRequest(
            title=request.title.strip() if request.title else template.task_title,
            description=template.task_description,
            type=template.task_type,
            priority=template.priority,
            estimated_points=float(template.estimated_points)
            if template.estimated_points is not None
            else None,
            assignee_id=(
                request.assignee_id
                if request.assignee_id is not None
                else template.default_assignee_id
            ),
            sprint_id=request.sprint_id,
            due_date=request.due_date,
        )

        task = await self.create_task(
            project_id=project_id,
            org_id=org_id,
            creator_id=actor_id,
            request=task_create_req,
        )

        # Clone checklist definitions if present
        if template.checklist_definitions:
            for idx, item_def in enumerate(template.checklist_definitions):
                title = (
                    item_def.get("title", f"Checklist item {idx + 1}")
                    if isinstance(item_def, dict)
                    else str(item_def)
                )
                c_assignee_raw = (
                    item_def.get("assignee_id") if isinstance(item_def, dict) else None
                )
                c_assignee = uuid.UUID(str(c_assignee_raw)) if c_assignee_raw else None
                await self.work_item_repo.create_checklist_item(
                    work_item_id=task.id,
                    title=title,
                    position=idx,
                    assignee_id=c_assignee,
                )
            await self.session.commit()
            await self.session.refresh(task)

        return task

    # ---------------- Phase 11: Recurring Tasks ---------------- #

    def _calculate_next_run(
        self, rule: RecurringTaskRule, base_time: datetime | None = None
    ) -> datetime:
        """Calculate next execution timestamp based on pattern and interval."""
        base = base_time or rule.next_run_at or datetime.now(UTC)
        interval = max(1, rule.interval_value)
        pattern = rule.recurrence_pattern.lower()

        if pattern == "daily":
            return base + timedelta(days=interval)
        elif pattern == "weekly":
            return base + timedelta(weeks=interval)
        elif pattern == "monthly":
            return base + timedelta(days=30 * interval)
        else:
            return base + timedelta(days=interval)

    async def create_recurring_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        creator_id: uuid.UUID,
        request: RecurringTaskRuleCreateRequest,
    ) -> RecurringTaskRule:
        """Create a scheduled recurring task generation rule."""
        await self.get_project_or_404(project_id, org_id)

        next_run = request.next_run_at or datetime.now(UTC)
        rule = await self.work_item_repo.create_recurring_rule(
            project_id=project_id,
            organization_id=org_id,
            title=request.title.strip(),
            description=request.description.strip() if request.description else None,
            type=request.type,
            priority=request.priority,
            estimated_points=request.estimated_points,
            assignee_id=request.assignee_id,
            recurrence_pattern=request.recurrence_pattern,
            interval_value=request.interval_value,
            timezone=request.timezone,
            next_run_at=next_run,
            is_active=request.is_active,
            created_by_id=creator_id,
        )
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def get_recurring_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
    ) -> RecurringTaskRule:
        """Get a specific recurring task rule by ID."""
        await self.get_project_or_404(project_id, org_id)
        rule = await self.work_item_repo.get_recurring_rule(
            rule_id, project_id=project_id
        )
        if rule is None:
            raise DomainException(
                code="RECURRING_RULE_NOT_FOUND",
                message="Recurring task rule not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"rule_id": str(rule_id)},
            )
        return rule

    async def list_recurring_rules(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        active_only: bool = False,
    ) -> list[RecurringTaskRule]:
        """List all recurring task rules defined in a project."""
        await self.get_project_or_404(project_id, org_id)
        return await self.work_item_repo.list_recurring_rules(
            project_id, active_only=active_only
        )

    async def update_recurring_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
        request: RecurringTaskRuleUpdateRequest,
    ) -> RecurringTaskRule:
        """Update scheduling or blueprint fields of a recurring task rule."""
        rule = await self.get_recurring_rule(project_id, org_id, rule_id)

        kwargs: dict[str, Any] = {}
        if request.title is not None:
            kwargs["title"] = request.title.strip()
        if request.description is not None:
            kwargs["description"] = (
                request.description.strip() if request.description else None
            )
        if request.type is not None:
            kwargs["type"] = request.type
        if request.priority is not None:
            kwargs["priority"] = request.priority
        if request.estimated_points is not None:
            kwargs["estimated_points"] = request.estimated_points
        if "assignee_id" in request.model_fields_set:
            kwargs["assignee_id"] = request.assignee_id
        if request.recurrence_pattern is not None:
            kwargs["recurrence_pattern"] = request.recurrence_pattern
        if request.interval_value is not None:
            kwargs["interval_value"] = request.interval_value
        if request.timezone is not None:
            kwargs["timezone"] = request.timezone
        if request.next_run_at is not None:
            kwargs["next_run_at"] = request.next_run_at
        if request.is_active is not None:
            kwargs["is_active"] = request.is_active

        updated = await self.work_item_repo.update_recurring_rule(rule, **kwargs)
        await self.session.commit()
        await self.session.refresh(updated)
        return updated

    async def delete_recurring_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
    ) -> None:
        """Delete a recurring task rule from a project."""
        rule = await self.get_recurring_rule(project_id, org_id, rule_id)
        await self.work_item_repo.delete_recurring_rule(rule)
        await self.session.commit()

    async def trigger_recurring_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
        rule_id: uuid.UUID,
    ) -> tuple[WorkItem, RecurringTaskRule]:
        """
        Manually or periodically trigger a recurring rule:
        Instantiates the task, advances next_run_at, and records last_run_at.
        """
        rule = await self.get_recurring_rule(project_id, org_id, rule_id)

        task_create_req = WorkItemCreateRequest(
            title=rule.title,
            description=rule.description,
            type=rule.type,
            priority=rule.priority,
            estimated_points=float(rule.estimated_points)
            if rule.estimated_points is not None
            else None,
            assignee_id=rule.assignee_id,
        )

        task = await self.create_task(
            project_id=project_id,
            org_id=org_id,
            creator_id=actor_id,
            request=task_create_req,
        )

        now = datetime.now(UTC)
        rule.last_run_at = now
        rule.next_run_at = self._calculate_next_run(rule, base_time=now)
        await self.session.commit()
        await self.session.refresh(rule)

        return task, rule

    # ---------------- Phase 11: Automation Rules & Engine ---------------- #

    async def create_automation_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        creator_id: uuid.UUID,
        request: AutomationRuleCreateRequest,
    ) -> AutomationRule:
        """Create a new event automation rule in a project."""
        await self.get_project_or_404(project_id, org_id)

        rule = await self.work_item_repo.create_automation_rule(
            project_id=project_id,
            organization_id=org_id,
            name=request.name.strip(),
            trigger_event=request.trigger_event,
            action_type=request.action_type,
            action_payload=request.action_payload,
            trigger_conditions=request.trigger_conditions,
            description=request.description.strip() if request.description else None,
            is_active=request.is_active,
            created_by_id=creator_id,
        )
        await self.session.commit()
        await self.session.refresh(rule)
        return rule

    async def get_automation_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
    ) -> AutomationRule:
        """Get a specific automation rule by ID."""
        await self.get_project_or_404(project_id, org_id)
        rule = await self.work_item_repo.get_automation_rule(
            rule_id, project_id=project_id
        )
        if rule is None:
            raise DomainException(
                code="AUTOMATION_RULE_NOT_FOUND",
                message="Automation rule not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"rule_id": str(rule_id)},
            )
        return rule

    async def list_automation_rules(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        trigger_event: str | None = None,
    ) -> list[AutomationRule]:
        """List all automation rules configured in a project."""
        await self.get_project_or_404(project_id, org_id)
        return await self.work_item_repo.list_automation_rules(
            project_id, trigger_event=trigger_event
        )

    async def update_automation_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
        request: AutomationRuleUpdateRequest,
    ) -> AutomationRule:
        """Update trigger or action parameters of an automation rule."""
        rule = await self.get_automation_rule(project_id, org_id, rule_id)

        kwargs: dict[str, Any] = {}
        if request.name is not None:
            kwargs["name"] = request.name.strip()
        if request.description is not None:
            kwargs["description"] = (
                request.description.strip() if request.description else None
            )
        if request.trigger_event is not None:
            kwargs["trigger_event"] = request.trigger_event
        if "trigger_conditions" in request.model_fields_set:
            kwargs["trigger_conditions"] = request.trigger_conditions
        if request.action_type is not None:
            kwargs["action_type"] = request.action_type
        if request.action_payload is not None:
            kwargs["action_payload"] = request.action_payload
        if request.is_active is not None:
            kwargs["is_active"] = request.is_active

        updated = await self.work_item_repo.update_automation_rule(rule, **kwargs)
        await self.session.commit()
        await self.session.refresh(updated)
        return updated

    async def delete_automation_rule(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
    ) -> None:
        """Delete an automation rule from a project."""
        rule = await self.get_automation_rule(project_id, org_id, rule_id)
        await self.work_item_repo.delete_automation_rule(rule)
        await self.session.commit()

    async def list_automation_logs(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        rule_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> list[AutomationLog]:
        """List execution audit history logs for automations in a project."""
        await self.get_project_or_404(project_id, org_id)
        return await self.work_item_repo.list_automation_logs(
            project_id, rule_id=rule_id, limit=limit
        )

    async def dry_run_automation(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        rule_id: uuid.UUID,
        request: AutomationDryRunRequest,
    ) -> AutomationDryRunResponse:
        """
        Simulate an automation rule against a task without persisting modifications.
        """
        rule = await self.get_automation_rule(project_id, org_id, rule_id)
        task = await self.get_task_by_id(request.work_item_id, project_id=project_id)

        matched = True
        reason = "Task satisfies rule trigger conditions."
        if rule.trigger_conditions:
            for field_name, expected_val in rule.trigger_conditions.items():
                task_val = getattr(task, field_name, None)
                if task_val is None and field_name == "assignee_id":
                    task_val = str(task.assignee_id) if task.assignee_id else None
                if str(task_val).lower() != str(expected_val).lower():
                    matched = False
                    reason = (
                        f"Field '{field_name}' had value '{task_val}', "
                        f"expected '{expected_val}'."
                    )
                    break

        projected: dict[str, Any] = {}
        if matched:
            projected = {rule.action_type: rule.action_payload}

        return AutomationDryRunResponse(
            matched=matched,
            action_type=rule.action_type,
            projected_action=projected,
            reason=reason,
        )

    async def execute_automations(
        self,
        project_id: uuid.UUID,
        trigger_event: str,
        task: WorkItem,
        actor_id: uuid.UUID | None = None,
        depth: int = 0,
    ) -> None:
        """
        Evaluate and execute active automation rules for a triggered event.
        Guarded against infinite cascade loops with max execution depth of 2.
        """
        if depth > 1:
            return

        rules = await self.work_item_repo.list_automation_rules(
            project_id=project_id,
            trigger_event=trigger_event,
            active_only=True,
        )
        if not rules:
            return

        for rule in rules:
            # Evaluate trigger conditions
            matched = True
            if rule.trigger_conditions:
                for field_name, expected_val in rule.trigger_conditions.items():
                    actual_val = getattr(task, field_name, None)
                    if actual_val is None and field_name == "assignee_id":
                        actual_val = str(task.assignee_id) if task.assignee_id else None
                    if str(actual_val).lower() != str(expected_val).lower():
                        matched = False
                        break

            if not matched:
                continue

            action_type = rule.action_type
            payload = rule.action_payload
            action_desc = f"Executed {action_type}"
            details: dict[str, Any] = {"payload": payload}

            try:
                if action_type == "assign_user":
                    user_id_raw = payload.get("user_id")
                    if user_id_raw:
                        target_user = uuid.UUID(str(user_id_raw))
                        task.assignee_id = target_user
                        action_desc = f"Assigned task to user {target_user}"
                        await self.work_item_repo.record_activity(
                            task.id, actor_id, "assignee_id", None, str(target_user)
                        )
                        if depth == 0:
                            await self.execute_automations(
                                project_id,
                                "task_assigned",
                                task,
                                actor_id,
                                depth=depth + 1,
                            )

                elif action_type == "change_status":
                    status_name = payload.get("status")
                    if status_name:
                        st_obj = await self.work_item_repo.get_status_by_name_or_id(
                            project_id, status_name
                        )
                        target_status = (
                            st_obj.category.lower()
                            if st_obj and st_obj.category
                            else status_name.lower().replace(" ", "_")
                        )
                        old_status = task.status
                        task.status = target_status
                        if target_status in COMPLETED_STATUSES:
                            task.completed_at = datetime.now(UTC)
                        action_desc = f"Changed status to {target_status}"
                        await self.work_item_repo.record_activity(
                            task.id, actor_id, "status", old_status, target_status
                        )
                        if depth == 0:
                            await self.execute_automations(
                                project_id,
                                "task_status_changed",
                                task,
                                actor_id,
                                depth=depth + 1,
                            )
                            if target_status in COMPLETED_STATUSES:
                                await self.execute_automations(
                                    project_id,
                                    "task_completed",
                                    task,
                                    actor_id,
                                    depth=depth + 1,
                                )

                elif action_type == "update_priority":
                    new_priority = payload.get("priority")
                    if new_priority:
                        old_pri = task.priority
                        task.priority = new_priority
                        action_desc = f"Updated priority to {new_priority}"
                        await self.work_item_repo.record_activity(
                            task.id, actor_id, "priority", old_pri, new_priority
                        )

                elif action_type == "send_notification":
                    notif_user_id = payload.get("user_id")
                    notif_msg = payload.get(
                        "message", f"Automation alert for {task.issue_key}"
                    )
                    notif_title = payload.get("title", f"Automation Rule: {rule.name}")
                    if notif_user_id:
                        target_notif_user = uuid.UUID(str(notif_user_id))
                        await self.collab_repo.create_notification(
                            user_id=target_notif_user,
                            actor_id=actor_id,
                            organization_id=task.organization_id,
                            type="automation_triggered",
                            title=notif_title,
                            message=notif_msg,
                            resource_type="task",
                            resource_id=task.id,
                        )
                        action_desc = f"Sent notification to user {target_notif_user}"

                rule.execution_count += 1
                rule.last_executed_at = datetime.now(UTC)
                await self.work_item_repo.create_automation_log(
                    rule_id=rule.id,
                    project_id=project_id,
                    work_item_id=task.id,
                    trigger_event=trigger_event,
                    status="success",
                    action_taken=action_desc,
                    details=details,
                )
                await self.session.commit()

            except Exception as exc:
                await self.work_item_repo.create_automation_log(
                    rule_id=rule.id,
                    project_id=project_id,
                    work_item_id=task.id,
                    trigger_event=trigger_event,
                    status="failed",
                    action_taken=f"Failed to execute {rule.action_type}",
                    error_message=str(exc),
                )
                await self.session.commit()

    # ---------------- Phase 11: Bulk Task Operations ---------------- #

    async def execute_bulk_operation(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: BulkTaskOperationRequest,
    ) -> BulkTaskOperationResponse:
        """
        Execute bounded bulk operations across multiple project tasks.
        Validates project boundaries, workflow policies, and dependency constraints.
        """
        await self.get_project_or_404(project_id, org_id)

        if len(request.task_ids) > settings.MAX_BULK_UPDATE_LIMIT:
            raise DomainException(
                code="BULK_LIMIT_EXCEEDED",
                message=(
                    f"Requested {len(request.task_ids)} tasks exceeds max "
                    f"allowed limit of {settings.MAX_BULK_UPDATE_LIMIT}."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
                details={
                    "max_limit": settings.MAX_BULK_UPDATE_LIMIT,
                    "submitted_count": len(request.task_ids),
                },
            )

        successful_ids: list[uuid.UUID] = []
        errors: list[BulkItemError] = []

        for tid in request.task_ids:
            task = await self.work_item_repo.get_by_id(tid)
            if (
                task is None
                or task.project_id != project_id
                or task.organization_id != org_id
            ):
                errors.append(
                    BulkItemError(
                        task_id=tid,
                        error_code="TASK_NOT_FOUND",
                        detail="Task not found in the target project.",
                    )
                )
                continue

            op = request.operation
            payload = request.payload

            if op == "update_status":
                target_status_name = payload.get("status")
                if not target_status_name:
                    errors.append(
                        BulkItemError(
                            task_id=tid,
                            error_code="INVALID_PAYLOAD",
                            detail="Missing 'status' in payload.",
                        )
                    )
                    continue

                target_st_obj = await self.work_item_repo.get_status_by_name_or_id(
                    project_id, target_status_name
                )
                if target_st_obj is None:
                    errors.append(
                        BulkItemError(
                            task_id=tid,
                            error_code="INVALID_STATUS",
                            detail=(
                                f"Status '{target_status_name}' is not recognized."
                            ),
                        )
                    )
                    continue

                if target_st_obj.name.lower() in ("to do", "todo"):
                    new_st = "todo"
                elif target_st_obj.name.lower() in ("in progress", "in_progress"):
                    new_st = "in_progress"
                elif target_st_obj.name.lower() in ("done",):
                    new_st = "done"
                else:
                    new_st = target_st_obj.name

                # Check dependency blocking
                is_completed = new_st in COMPLETED_STATUSES or (
                    target_st_obj.category
                    and target_st_obj.category.lower() in COMPLETED_STATUSES
                )
                if is_completed:
                    incomplete_preds = (
                        await self.work_item_repo.get_incomplete_predecessors(task.id)
                    )
                    if incomplete_preds:
                        pred_keys = [p.issue_key for p in incomplete_preds]
                        errors.append(
                            BulkItemError(
                                task_id=tid,
                                error_code="TASK_BLOCKED_BY_DEPENDENCY",
                                detail=(
                                    f"Task is blocked by incomplete tasks: "
                                    f"{', '.join(pred_keys)}."
                                ),
                            )
                        )
                        continue

                # Check workflow transitions if configured
                has_trans = await self.work_item_repo.has_configured_transitions(
                    project_id
                )
                if has_trans and new_st != task.status:
                    curr_st_obj = await self.work_item_repo.get_status_by_name_or_id(
                        project_id, task.status
                    )
                    if curr_st_obj is not None:
                        allowed_transitions = (
                            await self.work_item_repo.get_allowed_transitions(
                                curr_st_obj.id
                            )
                        )
                        allowed_to_ids = {t.to_status_id for t in allowed_transitions}
                        if target_st_obj.id not in allowed_to_ids:
                            errors.append(
                                BulkItemError(
                                    task_id=tid,
                                    error_code="INVALID_STATUS_TRANSITION",
                                    detail=(
                                        f"Transition from '{task.status}' to "
                                        f"'{target_status_name}' is not permitted."
                                    ),
                                )
                            )
                            continue

                old_st = task.status
                task.status = new_st
                task.completed_at = datetime.now(UTC) if is_completed else None
                await self.work_item_repo.record_activity(
                    task.id, actor_id, "status", old_st, new_st
                )
                successful_ids.append(tid)

            elif op == "assign_user":
                target_uid_raw = payload.get("assignee_id")
                if target_uid_raw is not None:
                    try:
                        user_uuid = uuid.UUID(str(target_uid_raw))
                        mem = await self.org_repo.get_membership(org_id, user_uuid)
                        if mem is None or not mem.is_active:
                            errors.append(
                                BulkItemError(
                                    task_id=tid,
                                    error_code="INVALID_ASSIGNEE",
                                    detail="Assignee is not an active member.",
                                )
                            )
                            continue
                        old_ass = str(task.assignee_id) if task.assignee_id else None
                        task.assignee_id = user_uuid
                        await self.work_item_repo.record_activity(
                            task.id, actor_id, "assignee_id", old_ass, str(user_uuid)
                        )
                    except ValueError:
                        errors.append(
                            BulkItemError(
                                task_id=tid,
                                error_code="INVALID_UUID",
                                detail="Provided assignee_id is not a valid UUID.",
                            )
                        )
                        continue
                else:
                    task.assignee_id = None
                successful_ids.append(tid)

            elif op == "update_priority":
                new_pri = payload.get("priority")
                if not new_pri:
                    errors.append(
                        BulkItemError(
                            task_id=tid,
                            error_code="INVALID_PAYLOAD",
                            detail="Missing 'priority' in payload.",
                        )
                    )
                    continue
                old_pri = task.priority
                task.priority = new_pri
                await self.work_item_repo.record_activity(
                    task.id, actor_id, "priority", old_pri, new_pri
                )
                successful_ids.append(tid)

            elif op == "add_label":
                lid_raw = payload.get("label_id")
                if not lid_raw:
                    errors.append(
                        BulkItemError(
                            task_id=tid,
                            error_code="INVALID_PAYLOAD",
                            detail="Missing 'label_id' in payload.",
                        )
                    )
                    continue
                try:
                    label_id = uuid.UUID(str(lid_raw))
                    label_obj = await self.work_item_repo.get_label_by_id(
                        label_id, org_id
                    )
                    if label_obj is None:
                        errors.append(
                            BulkItemError(
                                task_id=tid,
                                error_code="LABEL_NOT_FOUND",
                                detail="Label not found in organization.",
                            )
                        )
                        continue
                    await self.work_item_repo.add_label_to_item(task.id, label_id)
                    successful_ids.append(tid)
                except ValueError:
                    errors.append(
                        BulkItemError(
                            task_id=tid,
                            error_code="INVALID_UUID",
                            detail="Provided label_id is not a valid UUID.",
                        )
                    )
                    continue

            elif op == "delete":
                await self.work_item_repo.delete_work_item(task)
                successful_ids.append(tid)

        await self.session.commit()

        return BulkTaskOperationResponse(
            total_requested=len(request.task_ids),
            successful_count=len(successful_ids),
            failed_count=len(errors),
            errors=errors,
            affected_task_ids=successful_ids,
        )


class SavedFilterService:
    """Service managing persistent saved filters and views."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SavedFilterRepository(session)
        self.work_item_repo = WorkItemRepository(session)
        self.org_repo = OrganizationRepository(session)
        self.project_repo = ProjectRepository(session)
        self.task_service = TaskService(session)

    async def create_saved_filter(
        self,
        user_id: uuid.UUID,
        request: SavedFilterCreateRequest,
    ) -> SavedFilter:
        """Create a user-owned saved filter after validating scope and criteria."""
        if request.organization_id is not None:
            membership = await self.org_repo.get_membership(
                request.organization_id, user_id
            )
            if membership is None or not membership.is_active:
                raise DomainException(
                    code="NOT_AN_ORGANIZATION_MEMBER",
                    message="You are not an active member of this organization.",
                    status_code=status.HTTP_403_FORBIDDEN,
                )

        if request.project_id is not None:
            project = await self.project_repo.get_by_id(
                request.project_id, org_id=request.organization_id
            )
            if project is None:
                raise DomainException(
                    code="PROJECT_NOT_FOUND",
                    message="Project not found or inaccessible.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )

        filter_dict = request.filter_criteria.model_dump(mode="json", exclude_none=True)

        saved_filter = SavedFilter(
            user_id=user_id,
            organization_id=request.organization_id,
            project_id=request.project_id,
            name=request.name.strip(),
            description=request.description.strip() if request.description else None,
            filter_criteria=filter_dict,
            sort_by=request.sort_by,
            sort_dir=request.sort_dir,
            view_type=request.view_type,
            is_favorite=request.is_favorite,
        )
        created = await self.repo.create(saved_filter)
        await self.session.commit()
        return created

    async def get_saved_filter(
        self,
        filter_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> SavedFilter:
        """Fetch a saved filter enforcing strict ownership isolation."""
        sf = await self.repo.get_by_id(filter_id)
        if sf is None or sf.user_id != user_id:
            raise DomainException(
                code="SAVED_FILTER_NOT_FOUND",
                message="Saved filter not found or inaccessible.",
                status_code=status.HTTP_404_NOT_FOUND,
            )
        return sf

    async def list_saved_filters(
        self,
        user_id: uuid.UUID,
        organization_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> list[SavedFilter]:
        """List saved filters owned by the user."""
        return await self.repo.list_for_user(
            user_id=user_id,
            organization_id=organization_id,
            project_id=project_id,
        )

    async def update_saved_filter(
        self,
        filter_id: uuid.UUID,
        user_id: uuid.UUID,
        request: SavedFilterUpdateRequest,
    ) -> SavedFilter:
        """Update properties of a user's saved filter."""
        sf = await self.get_saved_filter(filter_id, user_id=user_id)
        if request.name is not None:
            sf.name = request.name.strip()
        if request.description is not None:
            sf.description = (
                request.description.strip() if request.description else None
            )
        if request.filter_criteria is not None:
            sf.filter_criteria = request.filter_criteria.model_dump(
                mode="json", exclude_none=True
            )
        if request.sort_by is not None:
            sf.sort_by = request.sort_by
        if request.sort_dir is not None:
            sf.sort_dir = request.sort_dir
        if request.view_type is not None:
            sf.view_type = request.view_type
        if request.is_favorite is not None:
            sf.is_favorite = request.is_favorite
        updated = await self.repo.update(sf)
        await self.session.commit()
        return updated

    async def delete_saved_filter(
        self,
        filter_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Delete a saved filter owned by the user."""
        sf = await self.get_saved_filter(filter_id, user_id=user_id)
        await self.repo.delete(sf)
        await self.session.commit()

    async def execute_saved_filter(
        self,
        filter_id: uuid.UUID,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[WorkItem], int]:
        """Execute saved filter criteria and return matching accessible tasks."""
        sf = await self.get_saved_filter(filter_id, user_id=user_id)
        crit = TaskFilterCriteria.model_validate(sf.filter_criteria)

        accessible_filter = DashboardRepository(
            self.session
        )._accessible_projects_subquery(user_id)

        assignee_id = user_id if crit.assigned_to_me else crit.assignee_id

        return await self.task_service.list_tasks(
            project_id=sf.project_id,
            organization_id=sf.organization_id,
            status=crit.status,
            assignee_id=assignee_id,
            reporter_id=crit.reporter_id,
            priority=crit.priority,
            type=crit.type,
            search=crit.search,
            due_date_from=crit.due_date_from,
            due_date_to=crit.due_date_to,
            is_overdue=crit.is_overdue,
            due_soon=crit.due_soon,
            is_completed=crit.is_completed,
            label_id=crit.label_id,
            sort_by=sf.sort_by or "created_at",
            sort_dir=sf.sort_dir or "desc",
            page=page,
            page_size=page_size,
            accessible_filter=accessible_filter,
        )
