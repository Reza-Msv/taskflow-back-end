import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from taskflow.domains.work_items.schemas import WorkItemResponse

# -------------------------------------------------------------
# Sprint CRUD Request & Response Schemas
# -------------------------------------------------------------


class SprintCreateRequest(BaseModel):
    """Payload to create a new Sprint in draft status."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Sprint title/name",
        examples=["Sprint 1 - Core Foundations"],
    )
    goal: str | None = Field(
        default=None,
        max_length=2000,
        description="Sprint overarching business/engineering goal",
        examples=["Complete user authentication and product backlog foundation."],
    )
    start_date: datetime | None = Field(
        default=None,
        description="Planned sprint start timestamp (UTC)",
    )
    end_date: datetime | None = Field(
        default=None,
        description="Planned sprint end timestamp (UTC)",
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "SprintCreateRequest":
        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                raise ValueError("Sprint end_date must be strictly after start_date.")
        return self


class SprintUpdateRequest(BaseModel):
    """Payload to modify sprint details."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated sprint name",
    )
    goal: str | None = Field(
        default=None,
        max_length=2000,
        description="Updated sprint goal",
    )
    start_date: datetime | None = Field(
        default=None,
        description="Updated start date timestamp (UTC)",
    )
    end_date: datetime | None = Field(
        default=None,
        description="Updated end date timestamp (UTC)",
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "SprintUpdateRequest":
        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                raise ValueError("Sprint end_date must be strictly after start_date.")
        return self


class SprintStartRequest(BaseModel):
    """Payload to initiate/start a Sprint (transitions status to 'active')."""

    start_date: datetime | None = Field(
        default=None,
        description="Explicit start timestamp (defaults to current time if omitted)",
    )
    end_date: datetime | None = Field(
        default=None,
        description="Target completion end timestamp (UTC)",
    )
    goal: str | None = Field(
        default=None,
        max_length=2000,
        description="Finalized sprint goal",
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "SprintStartRequest":
        if self.start_date and self.end_date:
            if self.end_date <= self.start_date:
                raise ValueError("Sprint end_date must be strictly after start_date.")
        return self


class SprintCompleteRequest(BaseModel):
    """
    Payload for completing an active Sprint.
    Never silently deletes unfinished tasks; moves them either to the Product Backlog
    or into another designated target sprint.
    """

    move_unfinished_to: str = Field(
        default="backlog",
        pattern="^(backlog|next_sprint)$",
        description="Strategy for incomplete tasks: 'backlog' or 'next_sprint'",
    )
    target_sprint_id: uuid.UUID | None = Field(
        default=None,
        description="Target draft sprint ID when rollover is 'next_sprint'",
    )


class SprintResponse(BaseModel):
    """Scrum sprint representation."""

    id: uuid.UUID = Field(..., description="Sprint unique identifier")
    project_id: uuid.UUID = Field(..., description="Associated project ID")
    name: str = Field(..., description="Sprint name")
    goal: str | None = Field(default=None, description="Sprint goal")
    status: str = Field(
        ...,
        description="Sprint status (draft, active, completed, cancelled)",
    )
    start_date: datetime | None = Field(default=None, description="Start date (UTC)")
    end_date: datetime | None = Field(default=None, description="End date (UTC)")
    completed_at: datetime | None = Field(
        default=None, description="Completion timestamp"
    )
    total_tasks: int = Field(default=0, description="Total assigned tasks in sprint")
    completed_tasks: int = Field(default=0, description="Count of completed tasks")
    total_points: float = Field(default=0.0, description="Total estimated story points")
    completed_points: float = Field(default=0.0, description="Completed story points")
    created_at: datetime = Field(..., description="Sprint creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")

    model_config = ConfigDict(from_attributes=True)


class SprintListResponse(BaseModel):
    """List of Sprints for a project."""

    items: list[SprintResponse] = Field(..., description="List of Sprints")
    total: int = Field(..., description="Total sprint count")


# -------------------------------------------------------------
# Backlog & Sprint Task Assignment Schemas
# -------------------------------------------------------------


class SprintAssignTasksRequest(BaseModel):
    """Payload to assign or move existing project tasks into a Sprint."""

    task_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of task/work-item UUIDs belonging to the project",
    )


class BacklogMoveTasksRequest(BaseModel):
    """Payload to move existing tasks back to the Product Backlog."""

    task_ids: list[uuid.UUID] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="List of task UUIDs to return/move to product backlog",
    )


class BacklogReorderRequest(BaseModel):
    """
    Payload to prioritize and reorder backlog tasks.
    Supports either explicit new rank string, relative positioning (before/after),
    or a full ordered list of task IDs.
    """

    task_id: uuid.UUID | None = Field(
        default=None,
        description="Target task ID being moved",
    )
    ordered_task_ids: list[uuid.UUID] | None = Field(
        default=None,
        description="Ordered sequence of task IDs to reprioritize sequentially",
    )
    before_task_id: uuid.UUID | None = Field(
        default=None,
        description="Task directly preceding the target in the new order",
    )
    after_task_id: uuid.UUID | None = Field(
        default=None,
        description="Task directly succeeding the target in the new order",
    )
    rank: str | None = Field(
        default=None,
        description="Explicit custom rank string",
    )


class BacklogSummaryResponse(BaseModel):
    """Paginated Product Backlog response with aggregated points and count metrics."""

    total_tasks: int = Field(..., description="Total backlog items matching filter")
    total_points: float = Field(
        ..., description="Sum of estimated story points in backlog"
    )
    unestimated_tasks: int = Field(
        ..., description="Count of backlog tasks without estimates"
    )
    items: list[WorkItemResponse] = Field(..., description="Paginated backlog tasks")
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Items per page")
    total: int = Field(..., description="Total items matching query")


# -------------------------------------------------------------
# Story Points & Estimation Schemas
# -------------------------------------------------------------


class EstimateTaskRequest(BaseModel):
    """
    Payload for estimating task story points.
    Enforces a non-negative, reasonable scale (0 to 1000).
    """

    estimated_points: float | None = Field(
        default=None,
        ge=0.0,
        le=1000.0,
        description=(
            "Story point estimate (e.g., 0, 0.5, 1, 2, 3, 5, 8, 13, 20). "
            "Pass null to clear."
        ),
    )


# -------------------------------------------------------------
# Sprint Metrics & Reports Schemas
# -------------------------------------------------------------


class SprintSummaryResponse(BaseModel):
    """Detailed progress and estimation breakdown for a Sprint."""

    sprint_id: uuid.UUID = Field(..., description="Sprint ID")
    sprint_name: str = Field(..., description="Sprint name")
    project_id: uuid.UUID = Field(..., description="Project ID")
    status: str = Field(..., description="Sprint status")
    start_date: datetime | None = Field(default=None, description="Start date (UTC)")
    end_date: datetime | None = Field(default=None, description="End date (UTC)")
    completed_at: datetime | None = Field(
        default=None, description="Completion timestamp (UTC)"
    )
    total_task_count: int = Field(..., description="Total tasks in sprint")
    completed_task_count: int = Field(..., description="Tasks marked completed")
    incomplete_task_count: int = Field(..., description="Incomplete tasks")
    remaining_task_count: int = Field(..., description="Remaining tasks")
    total_estimated_points: float = Field(
        ..., description="Total estimated story points"
    )
    completed_points: float = Field(..., description="Completed story points")
    remaining_points: float = Field(..., description="Remaining story points")
    unestimated_task_count: int = Field(
        ..., description="Tasks without story point estimate"
    )
    completion_percentage_tasks: float = Field(
        ..., description="Percentage of tasks completed (0.0 - 100.0)"
    )
    completion_percentage_points: float = Field(
        ..., description="Percentage of story points completed (0.0 - 100.0)"
    )


class BurndownDataPoint(BaseModel):
    """Single daily point along the sprint burndown timeline."""

    date: str = Field(..., description="Calendar date (YYYY-MM-DD)")
    remaining_story_points: float | None = Field(
        default=None,
        description="Remaining story points at end of day, or null if unavailable",
    )
    remaining_tasks: int | None = Field(
        default=None,
        description="Remaining task count at end of day, or null if unavailable",
    )
    completed_story_points: float = Field(
        default=0.0,
        description="Cumulative completed story points up to end of day",
    )
    completed_tasks: int = Field(
        default=0,
        description="Cumulative completed task count up to end of day",
    )
    ideal_story_points: float | None = Field(
        default=None,
        description="Linear guideline points value for this day",
    )
    ideal_tasks: float | None = Field(
        default=None,
        description="Linear guideline tasks value for this day",
    )
    is_historical_reconstructed: bool = Field(
        default=False,
        description="True if reconstructed using actual task audit activity stream",
    )
    data_available: bool = Field(
        default=True,
        description="True if actual historical or current data is recorded",
    )
    notes: str | None = Field(
        default=None,
        description="Documentation of any historical data limitations",
    )


class SprintBurndownResponse(BaseModel):
    """Burndown dataset tracking remaining work across the sprint schedule."""

    sprint_id: uuid.UUID = Field(..., description="Sprint ID")
    sprint_name: str = Field(..., description="Sprint name")
    start_date: datetime | None = Field(default=None, description="Sprint start date")
    end_date: datetime | None = Field(
        default=None, description="Sprint planned end date"
    )
    status: str = Field(..., description="Current sprint status")
    total_story_points: float = Field(..., description="Total committed story points")
    total_tasks: int = Field(..., description="Total committed tasks")
    data_points: list[BurndownDataPoint] = Field(
        ..., description="Daily time series data points"
    )
    metric_types: list[str] = Field(
        default=["story_points", "task_count"],
        description="Metrics tracked in this burndown series",
    )
    reconstruction_policy: str = Field(
        default="Reconstructed strictly from WorkItemActivity event logs",
        description="Description of data reconstruction logic",
    )


class SprintVelocityItem(BaseModel):
    """Historical sprint metrics for project velocity calculations."""

    sprint_id: uuid.UUID = Field(..., description="Sprint ID")
    sprint_name: str = Field(..., description="Sprint name")
    start_date: datetime | None = Field(default=None, description="Start date")
    end_date: datetime | None = Field(default=None, description="End date")
    completed_at: datetime | None = Field(
        default=None, description="Completion timestamp"
    )
    committed_points: float = Field(
        ..., description="Total points in sprint at completion"
    )
    completed_points: float = Field(..., description="Completed points in sprint")
    committed_tasks: int = Field(..., description="Total tasks in sprint")
    completed_tasks: int = Field(..., description="Completed tasks in sprint")


class ProjectVelocityResponse(BaseModel):
    """Velocity calculation across historical completed Sprints for a project."""

    project_id: uuid.UUID = Field(..., description="Project ID")
    completed_sprints_count: int = Field(
        ..., description="Number of completed sprints included in velocity"
    )
    average_velocity_points: float = Field(
        ..., description="Average completed story points per sprint"
    )
    average_velocity_tasks: float = Field(
        ..., description="Average completed tasks per sprint"
    )
    sprints: list[SprintVelocityItem] = Field(
        ..., description="Historical completed sprints in chronological order"
    )
    policy_notes: str = Field(
        default=(
            "Only fully completed sprints are included. Incomplete, draft, and "
            "cancelled sprints are strictly excluded to avoid distorting velocity."
        ),
        description="Velocity calculation policy explanation",
    )


# -------------------------------------------------------------
# Sprint Retrospective Schemas
# -------------------------------------------------------------


class SprintRetrospectiveRequest(BaseModel):
    """Payload to create or update a sprint retrospective."""

    what_went_well: str | None = Field(
        default=None,
        description="Positive accomplishments and successful practices",
    )
    what_could_be_improved: str | None = Field(
        default=None,
        description="Pain points, bottlenecks, or areas for team improvement",
    )
    action_items: str | None = Field(
        default=None,
        description="Actionable steps or commitments for upcoming sprints",
    )


class SprintRetrospectiveResponse(BaseModel):
    """Sprint retrospective review data."""

    id: uuid.UUID = Field(..., description="Retrospective ID")
    sprint_id: uuid.UUID = Field(..., description="Associated sprint ID")
    what_went_well: str | None = Field(default=None, description="What went well")
    what_could_be_improved: str | None = Field(
        default=None, description="What could be improved"
    )
    action_items: str | None = Field(default=None, description="Action items")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")

    model_config = ConfigDict(from_attributes=True)
