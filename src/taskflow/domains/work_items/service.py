import math
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.exceptions import DomainException
from taskflow.domains.collaboration.repository import CollaborationRepository
from taskflow.domains.identity.repository import UserRepository
from taskflow.domains.organizations.repository import OrganizationRepository
from taskflow.domains.projects.repository import ProjectRepository
from taskflow.domains.work_items.models import Attachment, WorkItem
from taskflow.domains.work_items.repository import (
    COMPLETED_STATUSES,
    DashboardRepository,
    WorkItemRepository,
)
from taskflow.domains.work_items.schemas import (
    AttachmentListResponse,
    AttachmentResponse,
    ChecklistItemCreateRequest,
    ChecklistItemResponse,
    ChecklistItemUpdateRequest,
    ChecklistListResponse,
    DashboardSummaryResponse,
    KanbanBoardResponse,
    KanbanColumn,
    OverdueUpcomingTasksResponse,
    ProjectBrief,
    ProjectSummary,
    SubtaskCreateRequest,
    TaskStatusMoveRequest,
    UserActivityActor,
    UserActivityItem,
    UserActivityListResponse,
    WorkflowStatusResponse,
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
        due_date_from: datetime | None = None,
        due_date_to: datetime | None = None,
        sort_by: str = "smart_order",
        sort_dir: str = "asc",
    ) -> WorkItemListDashboardResponse:
        """
        Get tasks assigned to user with filtering, pagination, and smart ordering.
        """
        now = datetime.now(UTC)
        skip = (page - 1) * page_size
        items, total = await self.repo.list_assigned_tasks(
            user_id=user_id,
            now=now,
            skip=skip,
            limit=page_size,
            status=status,
            project_id=project_id,
            due_date_from=due_date_from,
            due_date_to=due_date_to,
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

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.work_item_repo = WorkItemRepository(session)
        self.project_repo = ProjectRepository(session)
        self.org_repo = OrganizationRepository(session)
        self.user_repo = UserRepository(session)
        self.collab_repo = CollaborationRepository(session)

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
        project_id: uuid.UUID,
        status: str | None = None,
        assignee_id: uuid.UUID | None = None,
        priority: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
    ) -> tuple[list[WorkItem], int]:
        """List tasks for a project with pagination and filters."""
        skip = (page - 1) * page_size
        return await self.work_item_repo.list_work_items(
            project_id=project_id,
            status=status,
            assignee_id=assignee_id,
            priority=priority,
            search=search,
            skip=skip,
            limit=page_size,
        )

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
            due_date=request.due_date,
            estimated_points=request.estimated_points,
            rank=rank_val,
        )

        # Audit creation event in same transaction
        await self.work_item_repo.record_activity(
            work_item_id=task.id,
            user_id=creator_id,
            field="created",
            old_value=None,
            new_value=f"Created task {issue_key}",
        )

        # Notification on task assignment
        if request.assignee_id is not None and request.assignee_id != creator_id:
            await self.collab_repo.create_notification(
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
                await self.collab_repo.create_notification(
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

        if request.due_date is not None and request.due_date != task.due_date:
            await self.work_item_repo.record_activity(
                task.id,
                actor_id,
                "due_date",
                task.due_date.isoformat() if task.due_date else None,
                request.due_date.isoformat(),
            )
            task.due_date = request.due_date
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

        if mutated:
            task.version += 1
            await self.session.commit()
            await self.session.refresh(task)

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

        new_status_name = (
            target_status_obj.category.lower()
            if target_status_obj.category
            else target_status_obj.name.lower().replace(" ", "_")
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

            if task.assignee_id and task.assignee_id != actor_id:
                await self.collab_repo.create_notification(
                    user_id=task.assignee_id,
                    actor_id=actor_id,
                    organization_id=task.organization_id,
                    type="task_status_changed",
                    title=f"Status Changed on {task.issue_key}",
                    message=f"Task {task.issue_key} was moved to {new_status_name}",
                    resource_type="task",
                    resource_id=task.id,
                )

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
            due_date=task.due_date,
            estimated_points=task.estimated_points,
            version=task.version,
            created_at=task.created_at,
            updated_at=task.updated_at,
            project=project_brief,
            reporter=reporter_dto,
            assignee=assignee_dto,
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
