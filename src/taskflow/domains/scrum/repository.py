import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from taskflow.domains.scrum.models import Sprint, SprintRetrospective, SprintSnapshot
from taskflow.domains.work_items.models import WorkItem, WorkItemActivity

COMPLETED_STATUSES = {"done", "completed", "closed", "resolved"}


class ScrumRepository:
    """Repository handling persistence and aggregation for Scrum entities."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------
    # Sprint Queries & Persistence
    # -------------------------------------------------------------

    async def get_sprint_by_id(
        self,
        sprint_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
    ) -> Sprint | None:
        """Fetch a sprint by ID, optionally verifying project_id."""
        query = (
            select(Sprint)
            .where(Sprint.id == sprint_id)
            .options(
                selectinload(Sprint.snapshots),
                selectinload(Sprint.retrospective),
                selectinload(Sprint.work_items),
            )
        )
        if project_id is not None:
            query = query.where(Sprint.project_id == project_id)

        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def list_sprints(
        self,
        project_id: uuid.UUID,
        status: str | None = None,
    ) -> list[Sprint]:
        """List all sprints for a project, optionally filtered by status."""
        query = (
            select(Sprint)
            .where(Sprint.project_id == project_id)
            .options(
                selectinload(Sprint.snapshots),
                selectinload(Sprint.work_items),
            )
            .order_by(Sprint.created_at.desc())
        )
        if status:
            query = query.where(Sprint.status == status)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_active_sprint(
        self,
        project_id: uuid.UUID,
    ) -> Sprint | None:
        """Fetch the currently active sprint for a project, if any."""
        query = (
            select(Sprint)
            .where(
                Sprint.project_id == project_id,
                Sprint.status == "active",
            )
            .options(
                selectinload(Sprint.snapshots),
                selectinload(Sprint.work_items),
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def create_sprint(
        self,
        project_id: uuid.UUID,
        name: str,
        goal: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> Sprint:
        """Create a new sprint in draft status."""
        sprint = Sprint(
            project_id=project_id,
            name=name,
            goal=goal,
            status="draft",
            start_date=start_date,
            end_date=end_date,
        )
        self.session.add(sprint)
        await self.session.flush()
        return sprint

    async def update_sprint(
        self,
        sprint: Sprint,
        name: str | None = None,
        goal: str | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        status: str | None = None,
        completed_at: datetime | None = None,
    ) -> Sprint:
        """Update mutable fields of a sprint."""
        if name is not None:
            sprint.name = name
        if goal is not None:
            sprint.goal = goal
        if start_date is not None:
            sprint.start_date = start_date
        if end_date is not None:
            sprint.end_date = end_date
        if status is not None:
            sprint.status = status
        if completed_at is not None:
            sprint.completed_at = completed_at

        sprint.updated_at = datetime.now(UTC)
        await self.session.flush()
        return sprint

    async def delete_sprint(
        self,
        sprint: Sprint,
    ) -> None:
        """
        Delete a sprint. All associated work items have their sprint_id safely
        cleared to null (returning them to the product backlog) rather than deleted.
        """
        # Clear sprint_id from all work items belonging to this sprint
        items_query = select(WorkItem).where(WorkItem.sprint_id == sprint.id)
        res = await self.session.execute(items_query)
        items = list(res.scalars().all())
        for item in items:
            item.sprint_id = None

        await self.session.delete(sprint)
        await self.session.flush()

    # -------------------------------------------------------------
    # Backlog & Sprint Tasks
    # -------------------------------------------------------------

    async def list_backlog_tasks(
        self,
        project_id: uuid.UUID,
        status: str | None = None,
        priority: str | None = None,
        assignee_id: uuid.UUID | None = None,
        type: str | None = None,
        search: str | None = None,
        skip: int = 0,
        limit: int = 25,
        sort_by: str = "rank",
        sort_dir: str = "asc",
    ) -> tuple[list[WorkItem], int, float, int]:
        """
        Retrieve paginated backlog work items (where sprint_id is NULL) with aggregated
        metrics: total_count, total_points, unestimated_count.
        """
        base_filters = [
            WorkItem.project_id == project_id,
            WorkItem.sprint_id.is_(None),
        ]

        if status:
            base_filters.append(WorkItem.status == status)
        if priority:
            base_filters.append(WorkItem.priority == priority)
        if assignee_id is not None:
            base_filters.append(WorkItem.assignee_id == assignee_id)
        if type:
            base_filters.append(WorkItem.type == type)
        if search:
            search_pattern = f"%{search.strip()}%"
            base_filters.append(
                or_(
                    WorkItem.title.ilike(search_pattern),
                    WorkItem.description.ilike(search_pattern),
                    WorkItem.issue_key.ilike(search_pattern),
                )
            )

        # 1. Aggregations (total matching, total story points, unestimated count)
        agg_stmt = select(
            func.count(WorkItem.id),
            func.coalesce(func.sum(WorkItem.estimated_points), 0.0),
            func.count(WorkItem.id).filter(WorkItem.estimated_points.is_(None)),
        ).where(*base_filters)

        agg_res = await self.session.execute(agg_stmt)
        total_count, total_points, unestimated_count = agg_res.one()

        # 2. Paginated task list
        query = (
            select(WorkItem)
            .where(*base_filters)
            .options(
                selectinload(WorkItem.assignee),
                selectinload(WorkItem.reporter),
                selectinload(WorkItem.labels),
                selectinload(WorkItem.children),
            )
        )

        is_desc = sort_dir.lower() == "desc"
        if sort_by == "priority":
            query = query.order_by(
                WorkItem.priority.desc() if is_desc else WorkItem.priority.asc(),
                WorkItem.rank.asc(),
            )
        elif sort_by == "created_at":
            query = query.order_by(
                WorkItem.created_at.desc() if is_desc else WorkItem.created_at.asc()
            )
        elif sort_by == "due_date":
            query = query.order_by(
                WorkItem.due_date.desc() if is_desc else WorkItem.due_date.asc()
            )
        elif sort_by == "estimated_points":
            query = query.order_by(
                WorkItem.estimated_points.desc()
                if is_desc
                else WorkItem.estimated_points.asc()
            )
        else:
            # Default backlog ordering is strictly rank ascending
            query = query.order_by(
                WorkItem.rank.desc() if is_desc else WorkItem.rank.asc(),
                WorkItem.created_at.asc(),
            )

        query = query.offset(skip).limit(limit)
        items_res = await self.session.execute(query)
        items = list(items_res.scalars().all())

        return items, int(total_count), float(total_points), int(unestimated_count)

    async def list_sprint_tasks(
        self,
        sprint_id: uuid.UUID,
        project_id: uuid.UUID,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[WorkItem], int]:
        """Fetch tasks currently assigned to a sprint."""
        base_filters = [
            WorkItem.project_id == project_id,
            WorkItem.sprint_id == sprint_id,
        ]

        count_stmt = select(func.count(WorkItem.id)).where(*base_filters)
        count_res = await self.session.execute(count_stmt)
        total = count_res.scalar() or 0

        query = (
            select(WorkItem)
            .where(*base_filters)
            .options(
                selectinload(WorkItem.assignee),
                selectinload(WorkItem.reporter),
                selectinload(WorkItem.labels),
                selectinload(WorkItem.children),
            )
            .order_by(WorkItem.rank.asc(), WorkItem.created_at.asc())
            .offset(skip)
            .limit(limit)
        )
        res = await self.session.execute(query)
        return list(res.scalars().all()), total

    async def get_tasks_by_ids(
        self,
        task_ids: list[uuid.UUID],
        project_id: uuid.UUID,
    ) -> list[WorkItem]:
        """Retrieve tasks strictly verifying they belong to the specified project."""
        if not task_ids:
            return []
        query = select(WorkItem).where(
            WorkItem.id.in_(task_ids),
            WorkItem.project_id == project_id,
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_all_backlog_tasks_ordered(
        self,
        project_id: uuid.UUID,
    ) -> list[WorkItem]:
        """Fetch all backlog tasks for a project ordered by rank."""
        query = (
            select(WorkItem)
            .where(
                WorkItem.project_id == project_id,
                WorkItem.sprint_id.is_(None),
            )
            .order_by(WorkItem.rank.asc(), WorkItem.created_at.asc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def record_activity(
        self,
        work_item_id: uuid.UUID,
        user_id: uuid.UUID | None,
        field: str,
        old_value: str | None,
        new_value: str | None,
    ) -> WorkItemActivity:
        """Record an audit trail activity event for a task."""
        activity = WorkItemActivity(
            work_item_id=work_item_id,
            user_id=user_id,
            field=field,
            old_value=old_value,
            new_value=new_value,
            created_at=datetime.now(UTC),
        )
        self.session.add(activity)
        await self.session.flush()
        return activity

    # -------------------------------------------------------------
    # Snapshots & Retrospectives
    # -------------------------------------------------------------

    async def create_snapshot(
        self,
        sprint_id: uuid.UUID,
        snapshot_type: str,
        total_points: float,
        completed_points: float,
        total_items: int,
        completed_items: int,
        scope_added_items: int = 0,
        scope_removed_items: int = 0,
    ) -> SprintSnapshot:
        """Record scope and completion snapshot upon lifecycle events."""
        snapshot = SprintSnapshot(
            sprint_id=sprint_id,
            snapshot_type=snapshot_type,
            total_points=total_points,
            completed_points=completed_points,
            total_items=total_items,
            completed_items=completed_items,
            scope_added_items=scope_added_items,
            scope_removed_items=scope_removed_items,
            recorded_at=datetime.now(UTC),
        )
        self.session.add(snapshot)
        await self.session.flush()
        return snapshot

    async def get_snapshots_for_sprint(
        self,
        sprint_id: uuid.UUID,
    ) -> list[SprintSnapshot]:
        """Retrieve all recorded snapshots for a sprint in chronological order."""
        query = (
            select(SprintSnapshot)
            .where(SprintSnapshot.sprint_id == sprint_id)
            .order_by(SprintSnapshot.recorded_at.asc())
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_retrospective(
        self,
        sprint_id: uuid.UUID,
    ) -> SprintRetrospective | None:
        """Fetch retrospective review for a sprint."""
        query = select(SprintRetrospective).where(
            SprintRetrospective.sprint_id == sprint_id
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def save_retrospective(
        self,
        sprint_id: uuid.UUID,
        what_went_well: str | None = None,
        what_could_be_improved: str | None = None,
        action_items: str | None = None,
    ) -> SprintRetrospective:
        """Create or update a sprint retrospective."""
        retro = await self.get_retrospective(sprint_id)
        now = datetime.now(UTC)
        if retro is None:
            retro = SprintRetrospective(
                sprint_id=sprint_id,
                what_went_well=what_went_well,
                what_could_be_improved=what_could_be_improved,
                action_items=action_items,
                created_at=now,
                updated_at=now,
            )
            self.session.add(retro)
        else:
            if what_went_well is not None:
                retro.what_went_well = what_went_well
            if what_could_be_improved is not None:
                retro.what_could_be_improved = what_could_be_improved
            if action_items is not None:
                retro.action_items = action_items
            retro.updated_at = now

        await self.session.flush()
        return retro

    # -------------------------------------------------------------
    # Metrics & Historical Reporting Queries
    # -------------------------------------------------------------

    async def get_sprint_metrics(
        self,
        sprint_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Aggregate real-time metrics for tasks currently assigned to a sprint."""
        is_completed_cond = func.lower(WorkItem.status).in_(COMPLETED_STATUSES)

        stmt = select(
            func.count(WorkItem.id).label("total_tasks"),
            func.count(WorkItem.id).filter(is_completed_cond).label("completed_tasks"),
            func.coalesce(func.sum(WorkItem.estimated_points), 0.0).label(
                "total_points"
            ),
            func.coalesce(
                func.sum(WorkItem.estimated_points).filter(is_completed_cond),
                0.0,
            ).label("completed_points"),
            func.count(WorkItem.id)
            .filter(WorkItem.estimated_points.is_(None))
            .label("unestimated_count"),
        ).where(WorkItem.sprint_id == sprint_id)

        res = await self.session.execute(stmt)
        row = res.one()

        total_tasks = int(row.total_tasks or 0)
        completed_tasks = int(row.completed_tasks or 0)
        remaining_tasks = total_tasks - completed_tasks
        total_points = float(row.total_points or 0.0)
        completed_points = float(row.completed_points or 0.0)
        remaining_points = max(0.0, total_points - completed_points)
        unestimated = int(row.unestimated_count or 0)

        task_pct = (
            round((completed_tasks / total_tasks) * 100.0, 1)
            if total_tasks > 0
            else 0.0
        )
        point_pct = (
            round((completed_points / total_points) * 100.0, 1)
            if total_points > 0.0
            else 0.0
        )

        return {
            "total_task_count": total_tasks,
            "completed_task_count": completed_tasks,
            "incomplete_task_count": remaining_tasks,
            "remaining_task_count": remaining_tasks,
            "total_estimated_points": total_points,
            "completed_points": completed_points,
            "remaining_points": remaining_points,
            "unestimated_task_count": unestimated,
            "completion_percentage_tasks": task_pct,
            "completion_percentage_points": point_pct,
        }

    async def get_sprint_activities(
        self,
        sprint_id: uuid.UUID,
    ) -> list[WorkItemActivity]:
        """
        Fetch all audit activities for tasks currently assigned to this sprint or
        activities referencing this sprint ID, sorted chronologically.
        """
        query = (
            select(WorkItemActivity)
            .join(WorkItem, WorkItem.id == WorkItemActivity.work_item_id)
            .where(
                or_(
                    WorkItem.sprint_id == sprint_id,
                    WorkItemActivity.old_value == str(sprint_id),
                    WorkItemActivity.new_value == str(sprint_id),
                ),
                WorkItemActivity.field.in_(["status", "estimated_points", "sprint_id"]),
            )
            .order_by(WorkItemActivity.created_at.asc())
        )
        res = await self.session.execute(query)
        return list(res.scalars().all())

    async def get_completed_sprints(
        self,
        project_id: uuid.UUID,
    ) -> list[Sprint]:
        """Fetch all completed sprints for a project in completion order."""
        query = (
            select(Sprint)
            .where(
                Sprint.project_id == project_id,
                Sprint.status == "completed",
            )
            .options(
                selectinload(Sprint.snapshots),
                selectinload(Sprint.work_items),
            )
            .order_by(Sprint.completed_at.asc(), Sprint.created_at.asc())
        )
        res = await self.session.execute(query)
        return list(res.scalars().all())
