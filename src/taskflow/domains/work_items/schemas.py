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


# -------------------------------------------------------------
# Task Management & Workflow Schemas (Phase 3)
# -------------------------------------------------------------


class WorkItemResponse(BaseModel):
    """Full work item details DTO."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique work item identifier")
    project_id: uuid.UUID = Field(..., description="Project identifier")
    organization_id: uuid.UUID = Field(..., description="Organization identifier")
    sequence_number: int = Field(..., description="Sequential integer counter")
    issue_key: str = Field(..., description="Human-readable issue key (e.g. TF-101)")
    type: str = Field(..., description="Work item type (task, bug, story, epic, subtask)")
    title: str = Field(..., description="Summary title")
    description: str | None = Field(default=None, description="Markdown description")
    status: str = Field(..., description="Status (todo, in_progress, done, cancelled, etc.)")
    priority: str = Field(..., description="Priority (lowest, low, medium, high, urgent)")
    parent_id: uuid.UUID | None = Field(default=None, description="Parent work item ID")
    reporter_id: uuid.UUID | None = Field(default=None, description="Creating user ID")
    assignee_id: uuid.UUID | None = Field(default=None, description="Assigned user ID")
    sprint_id: uuid.UUID | None = Field(default=None, description="Assigned sprint ID")
    rank: str = Field(..., description="Lexicographical relative rank")
    due_date: datetime | None = Field(default=None, description="Due date timestamp (UTC)")
    estimated_points: float | None = Field(default=None, description="Story points estimate")
    version: int = Field(..., description="Optimistic concurrency version lock")
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last update timestamp (UTC)")


class WorkItemListResponse(BaseModel):
    """Paginated list of work items."""

    items: list[WorkItemResponse] = Field(..., description="List of work items")
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Number of items per page")
    total: int = Field(..., description="Total number of items")


class WorkItemCreateRequest(BaseModel):
    """Payload for creating a new work item / task."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Summary title",
        examples=["Implement Kanban board API"],
    )
    description: str | None = Field(
        default=None,
        description="Extended markdown description",
    )
    type: str = Field(
        default="task",
        description="Work item type: task, bug, story, epic, subtask",
    )
    priority: str = Field(
        default="medium",
        description="Priority: lowest, low, medium, high, urgent",
    )
    status: str | None = Field(
        default=None,
        description="Initial status name (defaults to project default or 'todo')",
    )
    assignee_id: uuid.UUID | None = Field(
        default=None,
        description="Assignee user ID (must belong to project/org)",
    )
    parent_id: uuid.UUID | None = Field(
        default=None,
        description="Parent work item ID (for subtasks)",
    )
    due_date: datetime | None = Field(
        default=None,
        description="Due date timestamp (UTC)",
    )
    estimated_points: float | None = Field(
        default=None,
        ge=0,
        description="Story points estimate",
    )
    rank: str | None = Field(
        default=None,
        description="Custom rank string for relative ordering",
    )


class WorkItemUpdateRequest(BaseModel):
    """Payload for updating work item fields."""

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated summary title",
    )
    description: str | None = Field(
        default=None,
        description="Updated markdown description",
    )
    priority: str | None = Field(
        default=None,
        description="Updated priority: lowest, low, medium, high, urgent",
    )
    assignee_id: uuid.UUID | None = Field(
        default=None,
        description="Updated assignee user ID (must belong to project/org)",
    )
    parent_id: uuid.UUID | None = Field(
        default=None,
        description="Updated parent work item ID",
    )
    due_date: datetime | None = Field(
        default=None,
        description="Updated due date timestamp (UTC)",
    )
    estimated_points: float | None = Field(
        default=None,
        ge=0,
        description="Updated story points estimate",
    )
    version: int | None = Field(
        default=None,
        description="Optimistic concurrency lock version check",
    )


class TaskStatusMoveRequest(BaseModel):
    """Payload for moving a task between Kanban columns or reordering."""

    status: str = Field(..., description="Target status name (e.g. todo, in_progress, done)")
    rank: str | None = Field(
        default=None,
        description="Optional new rank string for column/backlog ordering",
    )
    version: int | None = Field(
        default=None,
        description="Optimistic concurrency version check",
    )


class WorkflowStatusResponse(BaseModel):
    """Workflow column status details."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique status ID")
    project_id: uuid.UUID | None = Field(default=None, description="Associated project ID")
    name: str = Field(..., description="Status display name")
    category: str = Field(..., description="Status category: todo, in_progress, done, cancelled")
    position: int = Field(..., description="Column position order")
    is_default: bool = Field(..., description="Whether this is the default initial status")


class KanbanColumn(BaseModel):
    """Column representation for Kanban board view."""

    status: WorkflowStatusResponse = Field(..., description="Column status metadata")
    tasks: list[WorkItemResponse] = Field(
        default_factory=list, description="Tasks currently in this status column"
    )
    total_tasks: int = Field(0, description="Total count of tasks in this column")


class KanbanBoardResponse(BaseModel):
    """Full Kanban board view grouped by project workflow statuses."""

    project_id: uuid.UUID = Field(..., description="Project identifier")
    columns: list[KanbanColumn] = Field(
        default_factory=list, description="Ordered status columns"
    )
    total_tasks: int = Field(0, description="Total tasks across all columns")


class WorkItemActivityResponse(BaseModel):
    """Audit history record for a specific work item."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Activity event ID")
    work_item_id: uuid.UUID = Field(..., description="Associated work item ID")
    user_id: uuid.UUID | None = Field(default=None, description="Acting user ID")
    field: str = Field(..., description="Changed property name (e.g. created, status, assignee_id, priority, due_date)")
    old_value: str | None = Field(default=None, description="Value prior to change")
    new_value: str | None = Field(default=None, description="Value after change")
    created_at: datetime = Field(..., description="Event timestamp (UTC)")
    user: UserActivityActor | None = Field(default=None, description="Acting user details")


class WorkItemHistoryListResponse(BaseModel):
    """Paginated history entries for a work item."""

    items: list[WorkItemActivityResponse] = Field(..., description="List of history events")
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Number of items per page")
    total: int = Field(..., description="Total history records")
