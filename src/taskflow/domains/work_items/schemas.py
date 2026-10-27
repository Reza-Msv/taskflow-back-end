import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    type: str = Field(
        ..., description="Work item type (task, bug, story, epic, subtask)"
    )
    title: str = Field(..., description="Summary title")
    description: str | None = Field(default=None, description="Markdown description")
    status: str = Field(
        ..., description="Status (todo, in_progress, done, cancelled, etc.)"
    )
    priority: str = Field(
        ..., description="Priority (lowest, low, medium, high, urgent)"
    )
    parent_id: uuid.UUID | None = Field(default=None, description="Parent work item ID")
    reporter_id: uuid.UUID | None = Field(default=None, description="Creating user ID")
    assignee_id: uuid.UUID | None = Field(default=None, description="Assigned user ID")
    sprint_id: uuid.UUID | None = Field(default=None, description="Assigned sprint ID")
    rank: str = Field(..., description="Lexicographical relative rank")
    due_date: datetime | None = Field(
        default=None, description="Due date timestamp (UTC)"
    )
    start_date: datetime | None = Field(
        default=None, description="Start date timestamp (UTC)"
    )
    milestone_id: uuid.UUID | None = Field(
        default=None, description="Associated milestone ID"
    )
    completed_at: datetime | None = Field(
        default=None, description="Completion timestamp (UTC)"
    )
    estimated_points: float | None = Field(
        default=None, description="Story points estimate"
    )
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
    start_date: datetime | None = Field(
        default=None,
        description="Planned start date timestamp (UTC)",
    )
    due_date: datetime | None = Field(
        default=None,
        description="Due date timestamp (UTC)",
    )
    milestone_id: uuid.UUID | None = Field(
        default=None,
        description="Associated milestone ID",
    )
    estimated_points: float | None = Field(
        default=None,
        ge=0,
        description="Story points estimate",
    )
    sprint_id: uuid.UUID | None = Field(
        default=None,
        description="Assigned sprint ID",
    )
    rank: str | None = Field(
        default=None,
        description="Custom rank string for relative ordering",
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "WorkItemCreateRequest":
        if self.start_date is not None and self.due_date is not None:
            if self.due_date < self.start_date:
                raise ValueError("due_date cannot be earlier than start_date")
        return self


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
    start_date: datetime | None = Field(
        default=None,
        description="Updated start date timestamp (UTC)",
    )
    due_date: datetime | None = Field(
        default=None,
        description="Updated due date timestamp (UTC)",
    )
    milestone_id: uuid.UUID | None = Field(
        default=None,
        description="Updated milestone ID (or null to unassign)",
    )
    estimated_points: float | None = Field(
        default=None,
        ge=0,
        description="Updated story points estimate",
    )
    sprint_id: uuid.UUID | None = Field(
        default=None,
        description="Updated sprint ID (or null to unassign)",
    )
    rank: str | None = Field(
        default=None,
        description="Updated relative rank string",
    )
    version: int | None = Field(
        default=None,
        description="Optimistic concurrency lock version check",
    )

    @model_validator(mode="after")
    def validate_dates(self) -> "WorkItemUpdateRequest":
        if self.start_date is not None and self.due_date is not None:
            if self.due_date < self.start_date:
                raise ValueError("due_date cannot be earlier than start_date")
        return self


class TaskStatusMoveRequest(BaseModel):
    """Payload for moving a task between Kanban columns or reordering."""

    status: str = Field(
        ..., description="Target status name (e.g. todo, in_progress, done)"
    )
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
    project_id: uuid.UUID | None = Field(
        default=None, description="Associated project ID"
    )
    name: str = Field(..., description="Status display name")
    category: str = Field(
        ..., description="Status category: todo, in_progress, done, cancelled"
    )
    position: int = Field(..., description="Column position order")
    is_default: bool = Field(
        ..., description="Whether this is the default initial status"
    )


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
    field: str = Field(
        ...,
        description="Changed property (e.g. status, assignee_id, priority, due_date)",
    )
    old_value: str | None = Field(default=None, description="Value prior to change")
    new_value: str | None = Field(default=None, description="Value after change")
    created_at: datetime = Field(..., description="Event timestamp (UTC)")
    user: UserActivityActor | None = Field(
        default=None, description="Acting user details"
    )


class WorkItemHistoryListResponse(BaseModel):
    """Paginated history entries for a work item."""

    items: list[WorkItemActivityResponse] = Field(
        ..., description="List of history events"
    )
    page: int = Field(default=1, description="Current page number (1-indexed)")
    page_size: int = Field(default=25, description="Number of items per page")
    total: int = Field(..., description="Total history records")


# -------------------------------------------------------------
# Phase 4: Collaboration, Checklists & Attachments Schemas
# -------------------------------------------------------------


class ChecklistItemResponse(BaseModel):
    """Checklist item under a task."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique checklist item ID")
    work_item_id: uuid.UUID = Field(..., description="Associated task ID")
    title: str = Field(..., description="Checklist item content")
    is_completed: bool = Field(..., description="Whether item is marked complete")
    position: int = Field(..., description="Sorting order index")
    assignee_id: uuid.UUID | None = Field(default=None, description="Assigned user ID")
    assignee: UserActivityActor | None = Field(
        default=None, description="Assigned user details"
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last updated timestamp (UTC)")


class ChecklistItemCreateRequest(BaseModel):
    """Payload to create a new checklist item."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Checklist item text",
        examples=["Verify database indexes"],
    )
    assignee_id: uuid.UUID | None = Field(
        default=None,
        description="Optional assigned user ID",
    )
    position: int | None = Field(
        default=None,
        description="Optional sorting position order",
    )


class ChecklistItemUpdateRequest(BaseModel):
    """Payload to update an existing checklist item."""

    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated text",
    )
    is_completed: bool | None = Field(
        default=None,
        description="Updated completion status",
    )
    position: int | None = Field(
        default=None,
        description="Updated sort position",
    )
    assignee_id: uuid.UUID | None = Field(
        default=None,
        description="Updated assignee user ID",
    )


class ChecklistListResponse(BaseModel):
    """List of checklist items under a task."""

    items: list[ChecklistItemResponse] = Field(..., description="Checklist items")
    total: int = Field(..., description="Total checklist items count")


class AttachmentResponse(BaseModel):
    """File attachment metadata associated with a task."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique attachment ID")
    work_item_id: uuid.UUID = Field(..., description="Associated task ID")
    uploader_id: uuid.UUID | None = Field(default=None, description="Uploading user ID")
    file_name: str = Field(..., description="Original sanitized file name")
    file_size: int = Field(..., description="File size in bytes")
    content_type: str = Field(..., description="MIME content type")
    storage_path: str = Field(..., description="Stored file identifier")
    created_at: datetime = Field(..., description="Upload timestamp (UTC)")
    uploader: UserActivityActor | None = Field(
        default=None, description="Uploader user details"
    )


class AttachmentListResponse(BaseModel):
    """List of file attachments on a task."""

    items: list[AttachmentResponse] = Field(..., description="List of attachments")
    total: int = Field(..., description="Total attachments count")


class SubtaskCreateRequest(BaseModel):
    """Payload to create a child subtask under an existing parent task."""

    title: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Subtask title",
        examples=["Write integration tests"],
    )
    description: str | None = Field(
        default=None,
        description="Subtask markdown description",
    )
    priority: str = Field(
        default="medium",
        description="Priority: lowest, low, medium, high, urgent",
    )
    status: str | None = Field(
        default=None,
        description="Initial status name",
    )
    assignee_id: uuid.UUID | None = Field(
        default=None,
        description="Subtask assignee user ID",
    )
    due_date: datetime | None = Field(
        default=None,
        description="Subtask due date",
    )
    estimated_points: float | None = Field(
        default=None,
        ge=0,
        description="Story point estimate",
    )


# -------------------------------------------------------------
# Phase 8: Milestones & Task Dependencies Schemas
# -------------------------------------------------------------


class MilestoneBrief(BaseModel):
    """Compact milestone summary for tasks and timeline views."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique milestone identifier")
    name: str = Field(..., description="Milestone title")
    target_date: datetime = Field(
        ..., description="Target completion timestamp (UTC)"
    )
    is_completed: bool = Field(False, description="Completion status")


class MilestoneCreateRequest(BaseModel):
    """Payload for creating a new project milestone."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Milestone name",
        examples=["Beta Release v1.0"],
    )
    description: str | None = Field(
        default=None,
        description="Detailed milestone description",
    )
    target_date: datetime = Field(
        ...,
        description="Target completion timestamp (UTC)",
    )
    is_completed: bool = Field(
        default=False,
        description="Whether the milestone is initially completed",
    )


class MilestoneUpdateRequest(BaseModel):
    """Payload for updating an existing milestone."""

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Updated milestone name",
    )
    description: str | None = Field(
        default=None,
        description="Updated milestone description",
    )
    target_date: datetime | None = Field(
        default=None,
        description="Updated target completion timestamp (UTC)",
    )
    is_completed: bool | None = Field(
        default=None,
        description="Updated completion flag",
    )


class MilestoneResponse(BaseModel):
    """Full milestone representation including task statistics."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique milestone identifier")
    project_id: uuid.UUID = Field(..., description="Associated project ID")
    name: str = Field(..., description="Milestone name")
    description: str | None = Field(
        default=None, description="Milestone description"
    )
    target_date: datetime = Field(
        ..., description="Target completion timestamp (UTC)"
    )
    is_completed: bool = Field(..., description="Whether milestone is completed")
    total_tasks: int = Field(
        default=0, description="Total tasks attached to this milestone"
    )
    completed_tasks: int = Field(
        default=0, description="Completed tasks attached to this milestone"
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")
    updated_at: datetime = Field(..., description="Last updated timestamp (UTC)")


class MilestoneListResponse(BaseModel):
    """List of project milestones."""

    items: list[MilestoneResponse] = Field(..., description="Milestones")
    total: int = Field(..., description="Total milestones count")


class TaskDependencyCreateRequest(BaseModel):
    """Payload for establishing a dependency relationship between two tasks."""

    predecessor_id: uuid.UUID = Field(
        ...,
        description="Predecessor task ID (must be completed first)",
    )
    successor_id: uuid.UUID = Field(
        ...,
        description="Successor task ID (dependent task)",
    )
    dependency_type: str = Field(
        default="finish_to_start",
        description="Relationship type: finish_to_start",
    )

    @model_validator(mode="after")
    def validate_no_self_dependency(self) -> "TaskDependencyCreateRequest":
        if self.predecessor_id == self.successor_id:
            raise ValueError("A task cannot depend on itself")
        return self


class TaskDependencyResponse(BaseModel):
    """Task dependency edge response."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Unique dependency identifier")
    project_id: uuid.UUID = Field(..., description="Associated project ID")
    predecessor_id: uuid.UUID = Field(..., description="Predecessor task ID")
    successor_id: uuid.UUID = Field(..., description="Successor task ID")
    dependency_type: str = Field(
        ..., description="Dependency relationship type"
    )
    created_at: datetime = Field(..., description="Creation timestamp (UTC)")


class TaskDependencyListResponse(BaseModel):
    """List of project dependencies."""

    items: list[TaskDependencyResponse] = Field(
        ..., description="Dependencies"
    )
    total: int = Field(..., description="Total dependencies count")


class TaskDependenciesSummary(BaseModel):
    """Direct incoming and outgoing dependencies for a specific task."""

    predecessors: list[TaskDependencyResponse] = Field(
        default_factory=list,
        description="Tasks that this task depends on",
    )
    successors: list[TaskDependencyResponse] = Field(
        default_factory=list,
        description="Tasks that depend on this task",
    )


class WorkItemDetailResponse(WorkItemResponse):
    """Comprehensive work item detail response including collaboration context."""

    project: ProjectBrief | None = Field(
        default=None, description="Associated project details"
    )
    reporter: UserActivityActor | None = Field(
        default=None, description="Reporter user details"
    )
    assignee: UserActivityActor | None = Field(
        default=None, description="Assignee user details"
    )
    milestone: MilestoneBrief | None = Field(
        default=None, description="Associated milestone details"
    )
    dependencies: TaskDependenciesSummary | None = Field(
        default=None, description="Incoming and outgoing task dependencies"
    )
    subtasks: list[WorkItemResponse] = Field(
        default_factory=list, description="Child subtasks"
    )
    checklists: list[ChecklistItemResponse] = Field(
        default_factory=list, description="Checklist items"
    )
    attachments: list[AttachmentResponse] = Field(
        default_factory=list, description="Attached files"
    )
    subtasks_count: int = Field(
        default=0, description="Total child subtasks count"
    )
    checklists_count: int = Field(
        default=0, description="Total checklist items count"
    )
    completed_checklists_count: int = Field(
        default=0, description="Completed checklist items count"
    )
    attachments_count: int = Field(
        default=0, description="Total attachments count"
    )
    comments_count: int = Field(default=0, description="Total comments count")


# -------------------------------------------------------------
# Phase 5: Search, Filters & Views Schemas
# -------------------------------------------------------------


class TaskFilterCriteria(BaseModel):
    """Structured criteria for advanced task filtering."""

    status: str | None = Field(default=None, description="Status name")
    priority: str | None = Field(default=None, description="Priority level")
    type: str | None = Field(default=None, description="Task type")
    assignee_id: uuid.UUID | None = Field(default=None, description="Assignee user ID")
    reporter_id: uuid.UUID | None = Field(default=None, description="Reporter user ID")
    assigned_to_me: bool | None = Field(
        default=None, description="Filter assigned to current user"
    )
    due_date_from: datetime | None = Field(
        default=None, description="Due date start range"
    )
    due_date_to: datetime | None = Field(default=None, description="Due date end range")
    is_overdue: bool | None = Field(default=None, description="Filter overdue tasks")
    due_soon: bool | None = Field(
        default=None, description="Filter tasks due in next 7 days"
    )
    is_completed: bool | None = Field(
        default=None, description="Filter completed state"
    )
    label_id: uuid.UUID | None = Field(default=None, description="Taxonomic label ID")
    search: str | None = Field(default=None, description="Text search term")


class SavedFilterCreateRequest(BaseModel):
    """Payload to create a persistent saved filter."""

    name: str = Field(
        ..., min_length=1, max_length=100, description="Saved filter name"
    )
    description: str | None = Field(
        default=None, max_length=500, description="Description"
    )
    organization_id: uuid.UUID | None = Field(
        default=None, description="Organization scope"
    )
    project_id: uuid.UUID | None = Field(default=None, description="Project scope")
    filter_criteria: TaskFilterCriteria = Field(
        default_factory=TaskFilterCriteria, description="Filter rules"
    )
    sort_by: str | None = Field(
        default=None,
        pattern="^(smart_order|due_date|priority|created_at|updated_at|title|rank)$",
        description="Sort field",
    )
    sort_dir: str | None = Field(
        default="asc",
        pattern="^(asc|desc)$",
        description="Sort direction",
    )
    view_type: str = Field(
        default="list",
        pattern="^(list|board|calendar)$",
        description="View layout",
    )
    is_favorite: bool = Field(default=False, description="Pinned favorite status")


class SavedFilterUpdateRequest(BaseModel):
    """Payload to update an existing saved filter."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    filter_criteria: TaskFilterCriteria | None = None
    sort_by: str | None = Field(
        default=None,
        pattern="^(smart_order|due_date|priority|created_at|updated_at|title|rank)$",
    )
    sort_dir: str | None = Field(default=None, pattern="^(asc|desc)$")
    view_type: str | None = Field(default=None, pattern="^(list|board|calendar)$")
    is_favorite: bool | None = None


class SavedFilterResponse(BaseModel):
    """Saved filter entity representation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    organization_id: uuid.UUID | None
    project_id: uuid.UUID | None
    name: str
    description: str | None
    filter_criteria: dict
    sort_by: str | None
    sort_dir: str | None
    view_type: str
    is_favorite: bool
    created_at: datetime
    updated_at: datetime


class SavedFilterListResponse(BaseModel):
    """List of saved filters."""

    items: list[SavedFilterResponse]
    total: int


class SearchTaskResult(BaseModel):
    """Global search match for task entity."""

    id: uuid.UUID
    issue_key: str
    title: str
    description: str | None
    status: str
    priority: str
    type: str
    project_id: uuid.UUID
    project_name: str
    project_key: str
    organization_id: uuid.UUID
    matched_field: str
    entity_type: str = "task"


class SearchProjectResult(BaseModel):
    """Global search match for project entity."""

    id: uuid.UUID
    name: str
    key: str
    description: str | None
    organization_id: uuid.UUID
    is_private: bool
    matched_field: str
    entity_type: str = "project"


class GlobalSearchResponse(BaseModel):
    """Combined search results across tasks and projects."""

    query: str
    tasks: list[SearchTaskResult]
    projects: list[SearchProjectResult]
    total_tasks: int
    total_projects: int
    limit: int
    offset: int


# -------------------------------------------------------------
# Phase 8: Calendar & Timeline Views Schemas
# -------------------------------------------------------------


class CalendarTaskItem(BaseModel):
    """Task item formatted for calendar view rendering."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Task identifier")
    issue_key: str = Field(..., description="Issue key (e.g. PROJ-12)")
    type: str = Field(..., description="Task type")
    title: str = Field(..., description="Task title")
    status: str = Field(..., description="Status name")
    priority: str = Field(..., description="Priority")
    start_date: datetime | None = Field(
        default=None, description="Start date timestamp (UTC)"
    )
    due_date: datetime | None = Field(
        default=None, description="Due date timestamp (UTC)"
    )
    estimated_points: float | None = Field(
        default=None, description="Story points estimate"
    )
    assignee: UserActivityActor | None = Field(
        default=None, description="Assignee actor"
    )
    milestone_id: uuid.UUID | None = Field(
        default=None, description="Associated milestone ID"
    )
    milestone_name: str | None = Field(
        default=None, description="Associated milestone name"
    )


class CalendarResponse(BaseModel):
    """Response containing tasks for a calendar date window."""

    items: list[CalendarTaskItem] = Field(
        default_factory=list,
        description="Scheduled tasks overlapping or falling within the date range",
    )
    unscheduled_items: list[CalendarTaskItem] = Field(
        default_factory=list,
        description="Tasks in project without planned start or due date",
    )
    total: int = Field(..., description="Total scheduled tasks in requested range")
    start_date: datetime = Field(
        ..., description="Query range start timestamp (UTC)"
    )
    end_date: datetime = Field(
        ..., description="Query range end timestamp (UTC)"
    )


class TimelineTaskItem(BaseModel):
    """Task item tailored for Timeline / Gantt chart scheduling."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Task identifier")
    issue_key: str = Field(..., description="Issue key")
    type: str = Field(..., description="Task type")
    title: str = Field(..., description="Task title")
    status: str = Field(..., description="Status name")
    priority: str = Field(..., description="Priority")
    start_date: datetime | None = Field(
        default=None, description="Start date timestamp (UTC)"
    )
    due_date: datetime | None = Field(
        default=None, description="Due date timestamp (UTC)"
    )
    estimated_points: float | None = Field(
        default=None, description="Story points estimate"
    )
    rank: str = Field(..., description="Relative backlog rank")
    assignee: UserActivityActor | None = Field(
        default=None, description="Assignee actor"
    )
    milestone_id: uuid.UUID | None = Field(
        default=None, description="Associated milestone ID"
    )
    milestone_name: str | None = Field(
        default=None, description="Associated milestone name"
    )
    has_dates: bool = Field(
        default=True, description="Whether task has start or due date"
    )


class TimelineMilestoneItem(BaseModel):
    """Milestone marker for Timeline / Gantt chart."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Milestone identifier")
    name: str = Field(..., description="Milestone name")
    description: str | None = Field(
        default=None, description="Milestone description"
    )
    target_date: datetime = Field(
        ..., description="Target completion timestamp (UTC)"
    )
    is_completed: bool = Field(..., description="Whether milestone is completed")


class TimelineDependencyItem(BaseModel):
    """Dependency connection between two tasks on the timeline."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="Dependency identifier")
    predecessor_id: uuid.UUID = Field(..., description="Predecessor task ID")
    successor_id: uuid.UUID = Field(..., description="Successor task ID")
    dependency_type: str = Field(
        default="finish_to_start", description="Dependency type"
    )


class TimelineResponse(BaseModel):
    """Full Gantt/Timeline view data payload."""

    project: ProjectBrief = Field(..., description="Project details")
    tasks: list[TimelineTaskItem] = Field(
        default_factory=list, description="Project tasks"
    )
    milestones: list[TimelineMilestoneItem] = Field(
        default_factory=list, description="Project milestones"
    )
    dependencies: list[TimelineDependencyItem] = Field(
        default_factory=list, description="Task dependencies"
    )
    start_date: datetime | None = Field(
        default=None, description="Timeline view window start if specified"
    )
    end_date: datetime | None = Field(
        default=None, description="Timeline view window end if specified"
    )


