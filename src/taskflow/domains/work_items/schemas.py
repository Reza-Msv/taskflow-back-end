import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectBrief(BaseModel):
    """Minimal project reference for tasks and summaries."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique project identifier")
    name: str = Field(..., description="Project name")
    key: str = Field(..., description="Project issue key prefix")
    organization_id: uuid.UUID = Field(..., description="Organization identifier")
    is_private: bool = Field(..., description="Whether the project is private")


class ProjectSummary(BaseModel):
    """Summary of a project accessible to the user with task metrics."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique project identifier")
    name: str = Field(..., description="Project name")
    key: str = Field(..., description="Project issue key prefix")
    organization_id: uuid.UUID = Field(..., description="Organization identifier")
    is_private: bool = Field(..., description="Whether the project is private")
    role: str = Field(..., description="User role in the project or organization")
    total_tasks: int = Field(0, description="Total non-archived work items in project")
    assigned_tasks: int = Field(
        0, description="Work items in project assigned to current user"
    )


class DashboardSummaryResponse(BaseModel):
    """Aggregated dashboard metrics for the authenticated user."""

    total_projects: int = Field(..., description="Total accessible projects")
    projects: list[ProjectSummary] = Field(
        default_factory=list,
        description="Relevant project summaries accessible to the user",
    )
    status_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Task counts grouped by status across assigned tasks",
    )
    assigned_tasks_count: int = Field(
        ..., description="Total active tasks assigned to the user"
    )
    in_progress_tasks_count: int = Field(
        ..., description="Total in-progress tasks assigned to the user"
    )
    completed_tasks_count: int = Field(
        ..., description="Total completed tasks assigned to the user"
    )
    overdue_tasks_count: int = Field(
        ..., description="Total overdue uncompleted tasks assigned to the user"
    )


class WorkItemDashboardItem(BaseModel):
    """Task item representation tailored for dashboard lists."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique task identifier")
    issue_key: str = Field(..., description="Human-readable issue key (e.g. TF-1)")
    type: str = Field(
        ..., description="Work item type (task, bug, story, epic, subtask)"
    )
    title: str = Field(..., description="Task title")
    status: str = Field(..., description="Current status")
    priority: str = Field(..., description="Priority level")
    due_date: datetime | None = Field(None, description="Due date timestamp (UTC)")
    estimated_points: float | None = Field(None, description="Estimated story points")
    rank: str = Field(..., description="Relative backlog rank")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last updated timestamp (UTC)")
    project: ProjectBrief = Field(..., description="Associated project details")
    is_overdue: bool = Field(False, description="Whether the task is currently overdue")


class WorkItemListDashboardResponse(BaseModel):
    """Paginated list of dashboard tasks."""

    items: list[WorkItemDashboardItem] = Field(..., description="List of tasks")
    total: int = Field(..., description="Total matching items count")
    page: int = Field(..., description="Current page number (1-based)")
    page_size: int = Field(..., description="Page size limit")
    total_pages: int = Field(..., description="Total number of pages")


class OverdueUpcomingTasksResponse(BaseModel):
    """Grouped overdue and upcoming tasks approaching their due date."""

    overdue_tasks: list[WorkItemDashboardItem] = Field(
        default_factory=list, description="Overdue incomplete tasks"
    )
    upcoming_tasks: list[WorkItemDashboardItem] = Field(
        default_factory=list,
        description="Upcoming tasks with due dates in the near future",
    )


class UserActivityActor(BaseModel):
    """Actor identity who performed the activity."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="User identifier")
    email: str = Field(..., description="User email")
    full_name: str = Field(..., description="User full display name")


class UserActivityItem(BaseModel):
    """Auditable activity event for user dashboard stream."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Activity identifier")
    activity_type: str = Field(
        ...,
        description="Type of activity: status_change, assignment, comment, etc.",
    )
    field: str | None = Field(None, description="Modified field name")
    old_value: str | None = Field(None, description="Previous value")
    new_value: str | None = Field(None, description="New updated value")
    created_at: datetime = Field(..., description="Activity timestamp (UTC)")
    work_item_id: uuid.UUID | None = Field(None, description="Work item ID")
    work_item_key: str | None = Field(None, description="Work item issue key")
    work_item_title: str | None = Field(None, description="Work item title")
    project_id: uuid.UUID | None = Field(None, description="Associated project ID")
    project_name: str | None = Field(None, description="Associated project name")
    actor: UserActivityActor | None = Field(
        None, description="Actor who made the change"
    )
    details: str | None = Field(None, description="Human-readable description of event")


class UserActivityListResponse(BaseModel):
    """Paginated recent activity list."""

    items: list[UserActivityItem] = Field(..., description="List of activity events")
    total: int = Field(..., description="Total activities")
    page: int = Field(..., description="Current page")
    page_size: int = Field(..., description="Items per page")
    total_pages: int = Field(..., description="Total pages")
