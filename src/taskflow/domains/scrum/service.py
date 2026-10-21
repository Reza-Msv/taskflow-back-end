import uuid
from datetime import UTC, datetime, timedelta

from fastapi import status
from sqlalchemy.ext.asyncio import AsyncSession

from taskflow.core.exceptions import DomainException
from taskflow.domains.organizations.repository import OrganizationRepository
from taskflow.domains.projects.models import Project
from taskflow.domains.projects.repository import ProjectRepository
from taskflow.domains.scrum.models import Sprint, SprintSnapshot
from taskflow.domains.scrum.repository import COMPLETED_STATUSES, ScrumRepository
from taskflow.domains.scrum.schemas import (
    BacklogReorderRequest,
    BacklogSummaryResponse,
    BurndownDataPoint,
    EstimateTaskRequest,
    ProjectVelocityResponse,
    SprintBurndownResponse,
    SprintCompleteRequest,
    SprintCreateRequest,
    SprintResponse,
    SprintRetrospectiveRequest,
    SprintRetrospectiveResponse,
    SprintStartRequest,
    SprintSummaryResponse,
    SprintUpdateRequest,
    SprintVelocityItem,
)
from taskflow.domains.work_items.schemas import WorkItemResponse


class ScrumService:
    """
    Scrum and Sprint Management domain service enforcing strict multi-tenant boundaries,
    lifecycle state transitions, single active sprint policy, and audit-backed metrics.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.scrum_repo = ScrumRepository(session)
        self.project_repo = ProjectRepository(session)
        self.org_repo = OrganizationRepository(session)

    async def _verify_project(
        self,
        project_id: uuid.UUID,
        org_id: uuid.UUID,
    ) -> Project:
        """Fetch project ensuring it belongs to organization or raise 404."""
        project = await self.project_repo.get_by_id(project_id, org_id=org_id)
        if project is None:
            raise DomainException(
                code="PROJECT_NOT_FOUND",
                message="Project not found or inaccessible.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"project_id": str(project_id), "organization_id": str(org_id)},
            )
        return project

    # -------------------------------------------------------------
    # Sprint CRUD Operations
    # -------------------------------------------------------------

    async def create_sprint(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        request: SprintCreateRequest,
    ) -> SprintResponse:
        """Create a new Sprint in draft status."""
        await self._verify_project(project_id, org_id)

        sprint = await self.scrum_repo.create_sprint(
            project_id=project_id,
            name=request.name,
            goal=request.goal,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        await self.session.commit()
        await self.session.refresh(sprint)
        return await self._to_sprint_response(sprint)

    async def list_sprints(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        status_filter: str | None = None,
    ) -> list[SprintResponse]:
        """List all Sprints for a project."""
        await self._verify_project(project_id, org_id)
        sprints = await self.scrum_repo.list_sprints(
            project_id=project_id,
            status=status_filter,
        )
        return [await self._to_sprint_response(s) for s in sprints]

    async def get_sprint(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
    ) -> SprintResponse:
        """Retrieve a specific Sprint by ID."""
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"sprint_id": str(sprint_id)},
            )
        return await self._to_sprint_response(sprint)

    async def update_sprint(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
        request: SprintUpdateRequest,
    ) -> SprintResponse:
        """Update Sprint details."""
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"sprint_id": str(sprint_id)},
            )

        if sprint.status in {"completed", "cancelled"}:
            raise DomainException(
                code="SPRINT_NOT_EDITABLE",
                message=f"Cannot edit a sprint with status '{sprint.status}'.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Validate date consistency with existing dates if partial update
        new_start = (
            request.start_date if request.start_date is not None else sprint.start_date
        )
        new_end = request.end_date if request.end_date is not None else sprint.end_date
        if new_start and new_end and new_end <= new_start:
            raise DomainException(
                code="INVALID_SPRINT_DATES",
                message="Sprint end_date must be strictly after start_date.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        updated = await self.scrum_repo.update_sprint(
            sprint=sprint,
            name=request.name,
            goal=request.goal,
            start_date=request.start_date,
            end_date=request.end_date,
        )
        await self.session.commit()
        await self.session.refresh(updated)
        return await self._to_sprint_response(updated)

    async def delete_sprint(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
    ) -> None:
        """
        Delete a Sprint. Unassigns all tasks safely back to the product backlog.
        Completed sprints cannot be deleted to preserve audit and velocity integrity.
        """
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"sprint_id": str(sprint_id)},
            )

        if sprint.status == "completed":
            raise DomainException(
                code="CANNOT_DELETE_COMPLETED_SPRINT",
                message="Completed sprints cannot be deleted to preserve history.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        await self.scrum_repo.delete_sprint(sprint)
        await self.session.commit()

    # -------------------------------------------------------------
    # Sprint Lifecycle Transitions
    # -------------------------------------------------------------

    async def start_sprint(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
        request: SprintStartRequest,
    ) -> SprintResponse:
        """
        Transition Sprint from draft to active.
        Enforces:
        - Must be in 'draft' status.
        - Exactly ONE active sprint allowed per project.
        - start_date and end_date must be present and end_date > start_date.
        - Records a 'start' SprintSnapshot.
        """
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"sprint_id": str(sprint_id)},
            )

        if sprint.status != "draft":
            raise DomainException(
                code="INVALID_SPRINT_STATUS_TRANSITION",
                message=(
                    f"Only draft sprints can be started (status: '{sprint.status}')."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Enforce active sprint policy: At most one active sprint per project
        active_sprint = await self.scrum_repo.get_active_sprint(project_id)
        if active_sprint is not None:
            raise DomainException(
                code="ACTIVE_SPRINT_EXISTS",
                message=(
                    f"Project already has an active sprint ('{active_sprint.name}'). "
                    "Complete or cancel it before starting another."
                ),
                status_code=status.HTTP_409_CONFLICT,
                details={"active_sprint_id": str(active_sprint.id)},
            )

        # Determine finalized dates
        start_date = request.start_date or sprint.start_date or datetime.now(UTC)
        end_date = request.end_date or sprint.end_date
        if end_date is None:
            # Default to standard 2-week sprint if not specified
            end_date = start_date + timedelta(days=14)

        if end_date <= start_date:
            raise DomainException(
                code="INVALID_SPRINT_DATES",
                message="Sprint end_date must be strictly after start_date.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Aggregate current commitment for initial snapshot
        metrics = await self.scrum_repo.get_sprint_metrics(sprint.id)
        await self.scrum_repo.create_snapshot(
            sprint_id=sprint.id,
            snapshot_type="start",
            total_points=metrics["total_estimated_points"],
            completed_points=metrics["completed_points"],
            total_items=metrics["total_task_count"],
            completed_items=metrics["completed_task_count"],
        )

        goal = request.goal or sprint.goal
        updated = await self.scrum_repo.update_sprint(
            sprint=sprint,
            status="active",
            start_date=start_date,
            end_date=end_date,
            goal=goal,
        )
        await self.session.commit()
        await self.session.refresh(updated)
        return await self._to_sprint_response(updated)

    async def complete_sprint(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
        request: SprintCompleteRequest,
        actor_id: uuid.UUID | None = None,
    ) -> SprintResponse:
        """
        Transition Sprint from active to completed.
        Handles unfinished tasks by moving them to backlog or next draft sprint.
        Never silently deletes unfinished tasks.
        Records a 'complete' SprintSnapshot.
        """
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"sprint_id": str(sprint_id)},
            )

        if sprint.status != "active":
            raise DomainException(
                code="INVALID_SPRINT_STATUS_TRANSITION",
                message=(
                    f"Only active sprints can be completed (status: '{sprint.status}')."
                ),
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Validate target destination for unfinished tasks
        target_sprint: Sprint | None = None
        if request.move_unfinished_to == "next_sprint":
            if not request.target_sprint_id:
                raise DomainException(
                    code="TARGET_SPRINT_REQUIRED",
                    message="target_sprint_id is required when move is 'next_sprint'.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            target_sprint = await self.scrum_repo.get_sprint_by_id(
                request.target_sprint_id,
                project_id=project_id,
            )
            if target_sprint is None:
                raise DomainException(
                    code="TARGET_SPRINT_NOT_FOUND",
                    message="Target sprint not found in this project.",
                    status_code=status.HTTP_404_NOT_FOUND,
                )
            if target_sprint.status != "draft":
                raise DomainException(
                    code="TARGET_SPRINT_NOT_DRAFT",
                    message="Target sprint for rollover tasks must be 'draft'.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )

        # Process all tasks currently in this sprint
        tasks, _ = await self.scrum_repo.list_sprint_tasks(
            sprint_id=sprint.id,
            project_id=project_id,
            limit=500,
        )

        now = datetime.now(UTC)
        for task in tasks:
            is_completed = task.status.lower() in COMPLETED_STATUSES
            if not is_completed:
                # Incomplete task handling
                if request.move_unfinished_to == "next_sprint" and target_sprint:
                    old_val = str(sprint.id)
                    new_val = str(target_sprint.id)
                    task.sprint_id = target_sprint.id
                else:
                    old_val = str(sprint.id)
                    new_val = "backlog"
                    task.sprint_id = None

                task.version += 1
                await self.scrum_repo.record_activity(
                    work_item_id=task.id,
                    user_id=actor_id,
                    field="sprint_id",
                    old_value=old_val,
                    new_value=new_val,
                )

        # Record completion snapshot
        metrics = await self.scrum_repo.get_sprint_metrics(sprint.id)
        await self.scrum_repo.create_snapshot(
            sprint_id=sprint.id,
            snapshot_type="complete",
            total_points=metrics["total_estimated_points"],
            completed_points=metrics["completed_points"],
            total_items=metrics["total_task_count"],
            completed_items=metrics["completed_task_count"],
        )

        updated = await self.scrum_repo.update_sprint(
            sprint=sprint,
            status="completed",
            completed_at=now,
        )
        await self.session.commit()
        await self.session.refresh(updated)
        return await self._to_sprint_response(updated)

    # -------------------------------------------------------------
    # Product Backlog Operations
    # -------------------------------------------------------------

    async def get_backlog(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        status_filter: str | None = None,
        priority: str | None = None,
        assignee_id: uuid.UUID | None = None,
        type: str | None = None,
        search: str | None = None,
        page: int = 1,
        page_size: int = 25,
        sort_by: str = "rank",
        sort_dir: str = "asc",
    ) -> BacklogSummaryResponse:
        """Fetch paginated product backlog items with aggregated metrics."""
        await self._verify_project(project_id, org_id)
        skip = (page - 1) * page_size
        (
            items,
            total_count,
            total_points,
            unestimated_count,
        ) = await self.scrum_repo.list_backlog_tasks(
            project_id=project_id,
            status=status_filter,
            priority=priority,
            assignee_id=assignee_id,
            type=type,
            search=search,
            skip=skip,
            limit=page_size,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )

        return BacklogSummaryResponse(
            total_tasks=total_count,
            total_points=total_points,
            unestimated_tasks=unestimated_count,
            items=[WorkItemResponse.model_validate(item) for item in items],
            page=page,
            page_size=page_size,
            total=total_count,
        )

    async def move_tasks_to_backlog(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        task_ids: list[uuid.UUID],
        actor_id: uuid.UUID | None = None,
    ) -> list[WorkItemResponse]:
        """Move existing tasks back to the Product Backlog."""
        await self._verify_project(project_id, org_id)
        tasks = await self.scrum_repo.get_tasks_by_ids(task_ids, project_id)
        found_ids = {t.id for t in tasks}
        missing_ids = [str(tid) for tid in task_ids if tid not in found_ids]
        if missing_ids:
            raise DomainException(
                code="TASKS_NOT_FOUND",
                message=f"Tasks {missing_ids} not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        for task in tasks:
            if task.sprint_id is not None:
                old_sprint = str(task.sprint_id)
                task.sprint_id = None
                task.version += 1
                await self.scrum_repo.record_activity(
                    work_item_id=task.id,
                    user_id=actor_id,
                    field="sprint_id",
                    old_value=old_sprint,
                    new_value="backlog",
                )

        await self.session.commit()
        for t in tasks:
            await self.session.refresh(t)
        return [WorkItemResponse.model_validate(t) for t in tasks]

    async def assign_tasks_to_sprint(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
        task_ids: list[uuid.UUID],
        actor_id: uuid.UUID | None = None,
    ) -> list[WorkItemResponse]:
        """Assign existing project tasks into a designated Sprint."""
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
                details={"sprint_id": str(sprint_id)},
            )

        if sprint.status in {"completed", "cancelled"}:
            raise DomainException(
                code="SPRINT_NOT_EDITABLE",
                message=f"Cannot add tasks to a sprint with status '{sprint.status}'.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        tasks = await self.scrum_repo.get_tasks_by_ids(task_ids, project_id)
        found_ids = {t.id for t in tasks}
        missing_ids = [str(tid) for tid in task_ids if tid not in found_ids]
        if missing_ids:
            raise DomainException(
                code="CROSS_PROJECT_ASSIGNMENT_FORBIDDEN",
                message=f"Tasks {missing_ids} do not belong to project {project_id}.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        for task in tasks:
            if task.sprint_id != sprint.id:
                old_sprint = str(task.sprint_id) if task.sprint_id else "backlog"
                task.sprint_id = sprint.id
                task.version += 1
                await self.scrum_repo.record_activity(
                    work_item_id=task.id,
                    user_id=actor_id,
                    field="sprint_id",
                    old_value=old_sprint,
                    new_value=str(sprint.id),
                )

        await self.session.commit()
        for t in tasks:
            await self.session.refresh(t)
        return [WorkItemResponse.model_validate(t) for t in tasks]

    async def remove_task_from_sprint(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
        task_id: uuid.UUID,
        actor_id: uuid.UUID | None = None,
    ) -> WorkItemResponse:
        """Remove a task from a Sprint, returning it directly to the Product Backlog."""
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        tasks = await self.scrum_repo.get_tasks_by_ids([task_id], project_id)
        if not tasks or tasks[0].sprint_id != sprint_id:
            raise DomainException(
                code="TASK_NOT_IN_SPRINT",
                message="Task not found in this sprint.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        task = tasks[0]
        task.sprint_id = None
        task.version += 1
        await self.scrum_repo.record_activity(
            work_item_id=task.id,
            user_id=actor_id,
            field="sprint_id",
            old_value=str(sprint.id),
            new_value="backlog",
        )
        await self.session.commit()
        await self.session.refresh(task)
        return WorkItemResponse.model_validate(task)

    async def list_sprint_tasks(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[WorkItemResponse], int]:
        """Fetch all tasks associated with a Sprint."""
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        items, total = await self.scrum_repo.list_sprint_tasks(
            sprint_id=sprint_id,
            project_id=project_id,
            skip=skip,
            limit=limit,
        )
        return [WorkItemResponse.model_validate(t) for t in items], total

    async def reorder_backlog(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        request: BacklogReorderRequest,
        actor_id: uuid.UUID | None = None,
    ) -> list[WorkItemResponse]:
        """
        Reorder tasks in the Product Backlog.
        Guarantees deterministic, collision-free relative ranking.
        """
        await self._verify_project(project_id, org_id)
        all_backlog = await self.scrum_repo.get_all_backlog_tasks_ordered(project_id)
        id_to_task = {t.id: t for t in all_backlog}

        if request.ordered_task_ids:
            # Reorder according to the provided list
            for idx, tid in enumerate(request.ordered_task_ids):
                task = id_to_task.get(tid)
                if task:
                    new_rank = f"0|{idx * 10:06d}:"
                    if task.rank != new_rank:
                        await self.scrum_repo.record_activity(
                            work_item_id=task.id,
                            user_id=actor_id,
                            field="rank",
                            old_value=task.rank,
                            new_value=new_rank,
                        )
                        task.rank = new_rank
                        task.version += 1
        elif request.task_id and request.rank:
            # Direct rank string assignment
            task = id_to_task.get(request.task_id)
            if task and task.rank != request.rank:
                await self.scrum_repo.record_activity(
                    work_item_id=task.id,
                    user_id=actor_id,
                    field="rank",
                    old_value=task.rank,
                    new_value=request.rank,
                )
                task.rank = request.rank
                task.version += 1
        elif request.task_id and (request.before_task_id or request.after_task_id):
            # Positional reorder
            current_ids = [t.id for t in all_backlog if t.id != request.task_id]
            if request.before_task_id and request.before_task_id in current_ids:
                insert_idx = current_ids.index(request.before_task_id)
                current_ids.insert(insert_idx, request.task_id)
            elif request.after_task_id and request.after_task_id in current_ids:
                insert_idx = current_ids.index(request.after_task_id) + 1
                current_ids.insert(insert_idx, request.task_id)
            else:
                current_ids.append(request.task_id)

            for idx, tid in enumerate(current_ids):
                task = id_to_task.get(tid)
                if task:
                    new_rank = f"0|{idx * 10:06d}:"
                    if task.rank != new_rank:
                        await self.scrum_repo.record_activity(
                            work_item_id=task.id,
                            user_id=actor_id,
                            field="rank",
                            old_value=task.rank,
                            new_value=new_rank,
                        )
                        task.rank = new_rank
                        task.version += 1

        await self.session.commit()
        refreshed = await self.scrum_repo.get_all_backlog_tasks_ordered(project_id)
        return [WorkItemResponse.model_validate(t) for t in refreshed]

    # -------------------------------------------------------------
    # Story Points & Estimation
    # -------------------------------------------------------------

    async def estimate_task(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        task_id: uuid.UUID,
        request: EstimateTaskRequest,
        actor_id: uuid.UUID | None = None,
    ) -> WorkItemResponse:
        """Update story point estimate on an existing task with audit logging."""
        await self._verify_project(project_id, org_id)
        tasks = await self.scrum_repo.get_tasks_by_ids([task_id], project_id)
        if not tasks:
            raise DomainException(
                code="TASK_NOT_FOUND",
                message="Task not found in this project.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        task = tasks[0]
        new_val = request.estimated_points
        if task.estimated_points != new_val:
            old_str = (
                str(task.estimated_points)
                if task.estimated_points is not None
                else None
            )
            new_str = str(new_val) if new_val is not None else None
            await self.scrum_repo.record_activity(
                work_item_id=task.id,
                user_id=actor_id,
                field="estimated_points",
                old_value=old_str,
                new_value=new_str,
            )
            task.estimated_points = new_val
            task.version += 1
            await self.session.commit()
            await self.session.refresh(task)

        return WorkItemResponse.model_validate(task)

    # -------------------------------------------------------------
    # Sprint Metrics & Reports
    # -------------------------------------------------------------

    async def get_sprint_summary(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
    ) -> SprintSummaryResponse:
        """Calculate and return comprehensive sprint summary metrics."""
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        metrics = await self.scrum_repo.get_sprint_metrics(sprint.id)
        return SprintSummaryResponse(
            sprint_id=sprint.id,
            sprint_name=sprint.name,
            project_id=sprint.project_id,
            status=sprint.status,
            start_date=sprint.start_date,
            end_date=sprint.end_date,
            completed_at=sprint.completed_at,
            total_task_count=metrics["total_task_count"],
            completed_task_count=metrics["completed_task_count"],
            incomplete_task_count=metrics["incomplete_task_count"],
            remaining_task_count=metrics["remaining_task_count"],
            total_estimated_points=metrics["total_estimated_points"],
            completed_points=metrics["completed_points"],
            remaining_points=metrics["remaining_points"],
            unestimated_task_count=metrics["unestimated_task_count"],
            completion_percentage_tasks=metrics["completion_percentage_tasks"],
            completion_percentage_points=metrics["completion_percentage_points"],
        )

    async def get_sprint_burndown(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
    ) -> SprintBurndownResponse:
        """
        Reconstruct authentic burndown time series data from actual task audit logs
        and snapshots. Never fabricates historical progress.
        """
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        start_dt = sprint.start_date or sprint.created_at
        end_dt = sprint.end_date or (start_dt + timedelta(days=14))

        start_date = start_dt.date()
        end_date = end_dt.date()
        now_date = datetime.now(UTC).date()

        if sprint.status == "completed" and sprint.completed_at:
            cutoff_date = sprint.completed_at.date()
        elif sprint.status == "active":
            cutoff_date = min(end_date, now_date)
        else:
            # Draft or cancelled: burndown guideline only
            cutoff_date = start_date

        total_days = max(1, (end_date - start_date).days)

        # Current sprint tasks
        tasks, _ = await self.scrum_repo.list_sprint_tasks(
            sprint_id=sprint.id,
            project_id=project_id,
            limit=500,
        )

        total_story_points = sum(t.estimated_points or 0.0 for t in tasks)
        total_tasks = len(tasks)

        ideal_drop_points = total_story_points / total_days if total_days > 0 else 0.0
        ideal_drop_tasks = total_tasks / total_days if total_days > 0 else 0.0

        # Retrieve audit activities for tasks in sprint
        activities = await self.scrum_repo.get_sprint_activities(sprint.id)

        data_points: list[BurndownDataPoint] = []
        for day_idx in range(total_days + 1):
            cur_date = start_date + timedelta(days=day_idx)
            cur_date_str = cur_date.isoformat()

            ideal_pts = max(
                0.0, round(total_story_points - (day_idx * ideal_drop_points), 1)
            )
            ideal_tks = max(0.0, round(total_tasks - (day_idx * ideal_drop_tasks), 1))

            if cur_date <= cutoff_date:
                # Reconstruct historical day state up to cur_date end-of-day
                day_end_dt = datetime(
                    cur_date.year, cur_date.month, cur_date.day, 23, 59, 59, tzinfo=UTC
                )

                # For each task, backtrack or resolve state at day_end_dt
                rem_pts = 0.0
                comp_pts = 0.0
                rem_tasks = 0
                comp_tasks = 0

                for t in tasks:
                    t_points = t.estimated_points or 0.0
                    t_status = t.status.lower()

                    # Find status activities for this task occurring after day_end_dt
                    future_status_acts = [
                        a
                        for a in activities
                        if a.work_item_id == t.id
                        and a.field == "status"
                        and (
                            a.created_at.replace(tzinfo=UTC)
                            if a.created_at.tzinfo is None
                            else a.created_at.astimezone(UTC)
                        )
                        > day_end_dt
                    ]
                    if future_status_acts:
                        # Earliest future activity gives old_value as of this day
                        earliest_future = min(
                            future_status_acts,
                            key=lambda a: (
                                a.created_at.replace(tzinfo=UTC)
                                if a.created_at.tzinfo is None
                                else a.created_at.astimezone(UTC)
                            ),
                        )
                        if earliest_future.old_value:
                            t_status = earliest_future.old_value.lower()

                    # Find estimate activities after day_end_dt
                    future_est_acts = [
                        a
                        for a in activities
                        if a.work_item_id == t.id
                        and a.field == "estimated_points"
                        and (
                            a.created_at.replace(tzinfo=UTC)
                            if a.created_at.tzinfo is None
                            else a.created_at.astimezone(UTC)
                        )
                        > day_end_dt
                    ]
                    if future_est_acts:
                        earliest_future = min(
                            future_est_acts,
                            key=lambda a: (
                                a.created_at.replace(tzinfo=UTC)
                                if a.created_at.tzinfo is None
                                else a.created_at.astimezone(UTC)
                            ),
                        )
                        if earliest_future.old_value:
                            try:
                                t_points = float(earliest_future.old_value)
                            except ValueError:
                                pass

                    if t_status in COMPLETED_STATUSES:
                        comp_pts += t_points
                        comp_tasks += 1
                    else:
                        rem_pts += t_points
                        rem_tasks += 1

                data_points.append(
                    BurndownDataPoint(
                        date=cur_date_str,
                        remaining_story_points=round(rem_pts, 1),
                        remaining_tasks=rem_tasks,
                        completed_story_points=round(comp_pts, 1),
                        completed_tasks=comp_tasks,
                        ideal_story_points=ideal_pts,
                        ideal_tasks=ideal_tks,
                        is_historical_reconstructed=True,
                        data_available=True,
                    )
                )
            else:
                # Future sprint day
                data_points.append(
                    BurndownDataPoint(
                        date=cur_date_str,
                        remaining_story_points=None,
                        remaining_tasks=None,
                        completed_story_points=0.0,
                        completed_tasks=0,
                        ideal_story_points=ideal_pts,
                        ideal_tasks=ideal_tks,
                        is_historical_reconstructed=False,
                        data_available=False,
                        notes="Sprint day has not yet occurred.",
                    )
                )

        return SprintBurndownResponse(
            sprint_id=sprint.id,
            sprint_name=sprint.name,
            start_date=sprint.start_date,
            end_date=sprint.end_date,
            status=sprint.status,
            total_story_points=total_story_points,
            total_tasks=total_tasks,
            data_points=data_points,
        )

    async def get_project_velocity(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> ProjectVelocityResponse:
        """
        Calculate velocity across historical completed Sprints for a project.
        Strictly excludes incomplete/active/draft sprints.
        """
        await self._verify_project(project_id, org_id)
        completed_sprints = await self.scrum_repo.get_completed_sprints(project_id)

        items: list[SprintVelocityItem] = []
        total_comp_pts = 0.0
        total_comp_tasks = 0

        for sp in completed_sprints:
            # Check if complete snapshot exists for immutable historical fidelity
            complete_snap: SprintSnapshot | None = None
            start_snap: SprintSnapshot | None = None
            for snap in sp.snapshots:
                if snap.snapshot_type == "complete":
                    complete_snap = snap
                elif snap.snapshot_type == "start":
                    start_snap = snap

            if complete_snap:
                committed_pts = (
                    start_snap.total_points
                    if start_snap
                    else complete_snap.total_points
                )
                committed_tasks = (
                    start_snap.total_items if start_snap else complete_snap.total_items
                )
                completed_pts = complete_snap.completed_points
                completed_tasks = complete_snap.completed_items
            else:
                # Real-time task calculation for completed sprint
                tasks = sp.work_items
                committed_pts = sum(t.estimated_points or 0.0 for t in tasks)
                committed_tasks = len(tasks)
                completed_pts = sum(
                    t.estimated_points or 0.0
                    for t in tasks
                    if t.status.lower() in COMPLETED_STATUSES
                )
                completed_tasks = sum(
                    1 for t in tasks if t.status.lower() in COMPLETED_STATUSES
                )

            total_comp_pts += completed_pts
            total_comp_tasks += completed_tasks

            items.append(
                SprintVelocityItem(
                    sprint_id=sp.id,
                    sprint_name=sp.name,
                    start_date=sp.start_date,
                    end_date=sp.end_date,
                    completed_at=sp.completed_at,
                    committed_points=committed_pts,
                    completed_points=completed_pts,
                    committed_tasks=committed_tasks,
                    completed_tasks=completed_tasks,
                )
            )

        count = len(items)
        avg_pts = round(total_comp_pts / count, 1) if count > 0 else 0.0
        avg_tasks = round(total_comp_tasks / count, 1) if count > 0 else 0.0

        return ProjectVelocityResponse(
            project_id=project_id,
            completed_sprints_count=count,
            average_velocity_points=avg_pts,
            average_velocity_tasks=avg_tasks,
            sprints=items,
        )

    # -------------------------------------------------------------
    # Retrospective Operations
    # -------------------------------------------------------------

    async def get_retrospective(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
    ) -> SprintRetrospectiveResponse | None:
        """Fetch retrospective review for a sprint."""
        await self._verify_project(project_id, org_id)
        retro = await self.scrum_repo.get_retrospective(sprint_id)
        if retro is None:
            return None
        return SprintRetrospectiveResponse.model_validate(retro)

    async def save_retrospective(
        self,
        org_id: uuid.UUID,
        project_id: uuid.UUID,
        sprint_id: uuid.UUID,
        request: SprintRetrospectiveRequest,
    ) -> SprintRetrospectiveResponse:
        """Create or update a sprint retrospective."""
        await self._verify_project(project_id, org_id)
        sprint = await self.scrum_repo.get_sprint_by_id(
            sprint_id=sprint_id,
            project_id=project_id,
        )
        if sprint is None:
            raise DomainException(
                code="SPRINT_NOT_FOUND",
                message="Sprint not found.",
                status_code=status.HTTP_404_NOT_FOUND,
            )

        retro = await self.scrum_repo.save_retrospective(
            sprint_id=sprint_id,
            what_went_well=request.what_went_well,
            what_could_be_improved=request.what_could_be_improved,
            action_items=request.action_items,
        )
        await self.session.commit()
        await self.session.refresh(retro)
        return SprintRetrospectiveResponse.model_validate(retro)

    # -------------------------------------------------------------
    # Helper Serialization
    # -------------------------------------------------------------

    async def _to_sprint_response(self, sprint: Sprint) -> SprintResponse:
        """Serialize Sprint model with aggregated live metrics."""
        metrics = await self.scrum_repo.get_sprint_metrics(sprint.id)
        return SprintResponse(
            id=sprint.id,
            project_id=sprint.project_id,
            name=sprint.name,
            goal=sprint.goal,
            status=sprint.status,
            start_date=sprint.start_date,
            end_date=sprint.end_date,
            completed_at=sprint.completed_at,
            total_tasks=metrics["total_task_count"],
            completed_tasks=metrics["completed_task_count"],
            total_points=metrics["total_estimated_points"],
            completed_points=metrics["completed_points"],
            created_at=sprint.created_at,
            updated_at=sprint.updated_at,
        )
