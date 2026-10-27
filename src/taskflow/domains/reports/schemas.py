import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from taskflow.domains.scrum.schemas import (
    BurndownDataPoint,
    SprintVelocityItem,
)


class StatusDistributionItem(BaseModel):
    """Distribution of work items across a specific workflow status."""

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(
        ..., description="Workflow status name (e.g. To Do, In Progress, Done)"
    )
    category: str = Field(..., description="Status category (todo, in_progress, done)")
    count: int = Field(0, description="Number of tasks currently in this status")
    percentage: float = Field(
        0.0, description="Percentage of total eligible project tasks"
    )


class DistributionItem(BaseModel):
    """Generic breakdown item by attribute such as priority or type."""

    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., description="Attribute name (e.g. high, urgent, bug, story)")
    count: int = Field(0, description="Number of tasks with this attribute")
    percentage: float = Field(
        0.0, description="Percentage of total eligible project tasks"
    )


class ProjectOverviewReportResponse(BaseModel):
    """Comprehensive high-level health and metrics report for a project."""

    project_id: uuid.UUID = Field(..., description="Unique project identifier")
    total_tasks: int = Field(..., description="Total non-deleted tasks in the project")
    completed_tasks: int = Field(..., description="Completed tasks in the project")
    open_tasks: int = Field(..., description="Open / unstarted tasks")
    in_progress_tasks: int = Field(..., description="Tasks actively in progress")
    overdue_tasks: int = Field(..., description="Incomplete tasks past their due date")
    unassigned_tasks: int = Field(
        ..., description="Incomplete tasks without an assignee"
    )
    completion_percentage: float = Field(
        ..., description="Overall completion percentage (0.0 if zero tasks)"
    )
    due_soon_tasks: int = Field(
        ..., description="Incomplete tasks due within the configured window"
    )
    due_window_days: int = Field(
        ..., description="Configured forward-looking due window in days"
    )
    total_estimated_points: float = Field(
        0.0, description="Sum of story point estimates across all tasks"
    )
    completed_estimated_points: float = Field(
        0.0, description="Sum of story point estimates across completed tasks"
    )
    status_distribution: list[StatusDistributionItem] = Field(
        default_factory=list,
        description="Distribution grouped by project workflow statuses",
    )
    priority_distribution: list[DistributionItem] = Field(
        default_factory=list, description="Distribution grouped by priority"
    )
    type_distribution: list[DistributionItem] = Field(
        default_factory=list, description="Distribution grouped by task type"
    )
    generated_at: datetime = Field(..., description="Report generation timestamp (UTC)")


class TrendBucketItem(BaseModel):
    """A single discrete time bucket within a completion trend series."""

    bucket_start: datetime = Field(
        ..., description="Start timestamp of the bucket (inclusive)"
    )
    bucket_end: datetime = Field(
        ..., description="End timestamp of the bucket (inclusive)"
    )
    label: str = Field(
        ..., description="Human-readable bucket interval label (e.g. 2026-10-27)"
    )
    completed_tasks: int = Field(0, description="Tasks completed during this interval")
    completed_story_points: float = Field(
        0.0, description="Story points completed during this interval"
    )


class CompletionTrendsReportResponse(BaseModel):
    """Historical task completion trends partitioned into uniform time buckets."""

    project_id: uuid.UUID = Field(..., description="Unique project identifier")
    start_date: datetime = Field(..., description="Start of reporting window (UTC)")
    end_date: datetime = Field(..., description="End of reporting window (UTC)")
    interval: Literal["day", "week", "month"] = Field(
        ..., description="Bucket time grouping: day, week, or month"
    )
    total_completed_in_period: int = Field(
        ..., description="Total tasks completed across all intervals in the period"
    )
    total_points_completed_in_period: float = Field(
        0.0,
        description="Total story points completed across all intervals in the period",
    )
    buckets: list[TrendBucketItem] = Field(
        default_factory=list,
        description="Ordered time buckets covering the entire date window",
    )
    generated_at: datetime = Field(..., description="Report generation timestamp (UTC)")


class MemberWorkloadUser(BaseModel):
    """Summary identity information for a workload report member."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="User ID")
    email: str = Field(..., description="User email address")
    full_name: str = Field(..., description="User full name")


class MemberWorkloadItem(BaseModel):
    """Workload and task assignment metrics for an individual project member."""

    user: MemberWorkloadUser = Field(..., description="Project team member details")
    role: str | None = Field(default=None, description="Project membership role")
    assigned_tasks: int = Field(0, description="Total active incomplete tasks assigned")
    in_progress_tasks: int = Field(0, description="Active in-progress tasks assigned")
    overdue_tasks: int = Field(0, description="Incomplete overdue tasks assigned")
    completed_tasks: int = Field(
        0, description="Completed tasks assigned (or within window)"
    )
    total_story_points: float = Field(0.0, description="Sum of story points assigned")
    completed_story_points: float = Field(
        0.0, description="Sum of completed story points"
    )


class UnassignedWorkItem(BaseModel):
    """Workload metrics for project tasks lacking an assignee."""

    unassigned_tasks: int = Field(
        0, description="Total incomplete tasks without an assignee"
    )
    unassigned_in_progress_tasks: int = Field(
        0, description="In-progress tasks without an assignee"
    )
    unassigned_overdue_tasks: int = Field(
        0, description="Overdue tasks without an assignee"
    )
    unassigned_story_points: float = Field(
        0.0, description="Sum of story points on unassigned tasks"
    )


class TeamWorkloadReportResponse(BaseModel):
    """Team workload distribution across all project members and unassigned work."""

    project_id: uuid.UUID = Field(..., description="Unique project identifier")
    total_members: int = Field(
        ..., description="Number of active members in the project"
    )
    members: list[MemberWorkloadItem] = Field(
        default_factory=list, description="Workload breakdown per member"
    )
    unassigned_work: UnassignedWorkItem = Field(
        ..., description="Metrics for tasks not assigned to any user"
    )
    completed_since: datetime | None = Field(
        default=None,
        description="Optional filter threshold applied for completed tasks",
    )
    generated_at: datetime = Field(..., description="Report generation timestamp (UTC)")


class SprintAnalyticsReportResponse(BaseModel):
    """Comprehensive sprint analytics report with burndown and historical velocity."""

    project_id: uuid.UUID = Field(..., description="Unique project identifier")
    sprint_id: uuid.UUID | None = Field(
        default=None, description="Analyzed sprint identifier (if requested/active)"
    )
    sprint_name: str | None = Field(default=None, description="Analyzed sprint name")
    status: str | None = Field(default=None, description="Analyzed sprint status")
    start_date: datetime | None = Field(
        default=None, description="Sprint start timestamp"
    )
    end_date: datetime | None = Field(default=None, description="Sprint end timestamp")
    completed_at: datetime | None = Field(
        default=None, description="Sprint completion timestamp"
    )
    committed_points: float = Field(
        0.0, description="Initial or planned story points for the sprint"
    )
    completed_points: float = Field(
        0.0, description="Completed story points for the sprint"
    )
    committed_tasks: int = Field(0, description="Total tasks committed to the sprint")
    completed_tasks: int = Field(0, description="Tasks completed in the sprint")
    completion_percentage: float = Field(
        0.0, description="Percentage of sprint points completed"
    )
    burndown: list[BurndownDataPoint] = Field(
        default_factory=list, description="Daily burndown progression data points"
    )
    velocity_history: list[SprintVelocityItem] = Field(
        default_factory=list, description="Historical velocity across completed sprints"
    )
    average_velocity: float = Field(
        0.0, description="Average story points completed per sprint"
    )
    average_velocity_points: float = Field(
        0.0, description="Average story points completed per sprint"
    )
    average_velocity_tasks: float = Field(
        0.0, description="Average tasks completed per sprint"
    )
    generated_at: datetime = Field(..., description="Report generation timestamp (UTC)")
