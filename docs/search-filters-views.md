# Phase 5: Global Search, Advanced Filters, My Tasks & Saved Filters

## Overview

Phase 5 introduces enterprise-grade search and filtering infrastructure to TaskFlow:
1. **Global Search**: Unified, authenticated search across tasks, issue keys, descriptions, and projects with tenant-isolated permission enforcement.
2. **Advanced Task Filters**: Multi-criteria parameterized filtering across status, priority, type, reporter, assignee, assignment state, due date ranges, overdue, due soon, completion state, labels, and text search with safe deterministic sorting.
3. **My Tasks API**: Personal task views derived strictly from authenticated user sessions with timezone-aware overdue/due-soon calculations and filtering.
4. **Saved Filters**: User-owned persistent filter views with structured criteria validation, deterministic sorting, view preferences, and private ownership enforcement.

---

## 1. Global Search

### Endpoint
`GET /api/v1/search`

### Description
Performs authenticated search across tasks and projects accessible to the current user. Inaccessible projects and tasks from organizations or private projects the user does not belong to are never revealed.

### Query Parameters
| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `q` | `string` | **Yes** | — | Search term (min 1, max 100 characters). Matches task title, issue key, description, project name, key, and description. |
| `type` | `string` | No | `"all"` | Scope: `"all"`, `"tasks"`, or `"projects"`. |
| `organization_id` | `UUID` | No | `null` | Optional organization filter. |
| `project_id` | `UUID` | No | `null` | Optional project filter (automatically limits scope to tasks). |
| `limit` | `integer` | No | `20` | Max results per entity type (1-100). |
| `offset` | `integer` | No | `0` | Offset for pagination. |

### Response Schema (`GlobalSearchResponse`)
```json
{
  "query": "telemetry",
  "tasks": [
    {
      "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
      "issue_key": "LRM-1",
      "title": "Calibrate navigation telemetry",
      "description": "Ensure rover gyro sensors are aligned with coordinates.",
      "status": "todo",
      "priority": "high",
      "type": "task",
      "project_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
      "project_name": "Lunar Rover Mission",
      "project_key": "LRM",
      "organization_id": "4a2b9f3e-8c5d-4f1a-b0e2-6d8c9a0b1c2d",
      "matched_field": "title",
      "entity_type": "task"
    }
  ],
  "projects": [
    {
      "id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d",
      "name": "Lunar Rover Mission",
      "key": "LRM",
      "description": "Autonomous lunar exploration rover project",
      "organization_id": "4a2b9f3e-8c5d-4f1a-b0e2-6d8c9a0b1c2d",
      "is_private": false,
      "matched_field": "name",
      "entity_type": "project"
    }
  ],
  "total_tasks": 1,
  "total_projects": 1,
  "limit": 20,
  "offset": 0
}
```

---

## 2. Advanced Task Filters

### Endpoints
- `GET /api/v1/organizations/{organization_id}/projects/{project_id}/tasks`
- `GET /api/v1/tasks/me` (My Tasks)
- `GET /api/v1/dashboard/tasks` (Dashboard tasks)
- `GET /api/v1/saved-filters/{filter_id}/tasks` (Execute Saved Filter)

### Supported Query Parameters
| Parameter | Type | Description |
|---|---|---|
| `status` | `string` | Filter by status name (e.g. `todo`, `in_progress`, `done`). |
| `priority` | `string` | Filter by priority (`low`, `medium`, `high`, `urgent`). |
| `type` | `string` | Filter by task type (`task`, `bug`, `feature`, `improvement`, `epic`). |
| `assignee_id` | `UUID` | Filter by assigned user ID. |
| `assigned_to_me` | `boolean` | When true, filters by current authenticated user. |
| `reporter_id` | `UUID` | Filter by task creator / reporter. |
| `due_date_from` | `datetime` | Filter tasks due on or after this timestamp. |
| `due_date_to` | `datetime` | Filter tasks due on or before this timestamp. |
| `is_overdue` | `boolean` | When true, returns uncompleted tasks with due date in the past. |
| `due_soon` | `boolean` | When true, returns uncompleted tasks due within the next 48 hours. |
| `is_completed` | `boolean` | Filter by completion state (`done` status category). |
| `search` | `string` | Substring match on task title, issue key, or description. |
| `label_id` | `UUID` | Filter tasks tagged with a specific label. |
| `sort_by` | `string` | Sorting field: `created_at`, `updated_at`, `due_date`, `priority`, `title`, `rank`, or `my_tasks_smart`. |
| `sort_dir` | `string` | Sort direction: `asc` or `desc` (default `desc`). |
| `page` / `page_size` | `integer` | Standard 1-indexed pagination. |

### Validation Rules
- `due_date_from` cannot be after `due_date_to`. If violated, raises `DomainException(code="INVALID_DATE_RANGE", status_code=400)`.
- All sort fields and direction values are allowlisted to prevent SQL injection and unbounded queries.

---

## 3. My Tasks API

### Endpoint
`GET /api/v1/tasks/me`

### Description
Returns tasks assigned to the authenticated user across all projects and organizations the user belongs to. User identity is derived strictly from the verified session / Bearer token, never from client request parameters.

### Features
- Default ordering prioritizes actionable tasks: overdue items first, then upcoming due dates, then priority, with completed tasks last.
- Timezone-aware date calculations for `is_overdue` and `due_soon` comparisons.
- Supports full filtering suite (`status`, `priority`, `type`, `search`, `organization_id`, `project_id`, `is_overdue`, `due_soon`, `is_completed`, `due_date_from`, `due_date_to`).

---

## 4. Saved Filters API

### Endpoints
| Method | Route | Description |
|---|---|---|
| `POST` | `/api/v1/saved-filters` | Create a new saved filter view. |
| `GET` | `/api/v1/saved-filters` | List all saved filters owned by the authenticated user. |
| `GET` | `/api/v1/saved-filters/{filter_id}` | Retrieve a saved filter by ID. |
| `PATCH` | `/api/v1/saved-filters/{filter_id}` | Update filter criteria, name, or preferences. |
| `DELETE` | `/api/v1/saved-filters/{filter_id}` | Delete a saved filter. |
| `GET` | `/api/v1/saved-filters/{filter_id}/tasks` | Execute saved filter and return matching task items. |

### Private Ownership & Isolation
- Saved filters are strictly private to their owner.
- Any attempt by another user to view, edit, execute, or delete a saved filter returns `404 Not Found` without disclosing existence.

### Request Body (`SavedFilterCreateRequest`)
```json
{
  "name": "My Urgent Overdue Tasks",
  "description": "Tasks assigned to me that need immediate attention",
  "organization_id": "4a2b9f3e-8c5d-4f1a-b0e2-6d8c9a0b1c2d",
  "project_id": null,
  "filter_criteria": {
    "priority": "urgent",
    "is_overdue": true,
    "assigned_to_me": true
  },
  "sort_by": "due_date",
  "sort_dir": "asc",
  "view_type": "list",
  "is_favorite": true
}
```

---

## 5. Database Schema & Migration

### Table: `saved_filters`
- `id`: UUID (Primary Key)
- `user_id`: UUID (Foreign Key `users.id` ON DELETE CASCADE, indexed)
- `organization_id`: UUID (Foreign Key `organizations.id` ON DELETE CASCADE, nullable)
- `project_id`: UUID (Foreign Key `projects.id` ON DELETE CASCADE, nullable)
- `name`: VARCHAR(100), NOT NULL
- `description`: VARCHAR(500), NULLABLE
- `filter_criteria`: JSON, NOT NULL (stores validated `TaskFilterCriteria`)
- `sort_by`: VARCHAR(50), NULLABLE
- `sort_dir`: VARCHAR(10), NULLABLE
- `view_type`: VARCHAR(50), DEFAULT 'list', NOT NULL
- `is_favorite`: BOOLEAN, DEFAULT FALSE, NOT NULL
- `created_at`: TIMESTAMPTZ, NOT NULL
- `updated_at`: TIMESTAMPTZ, NOT NULL

### Migration
- Reversible Alembic migration `alembic/versions/20261020_0004_add_saved_filters.py`.
- Includes compound index `ix_saved_filters_org_user` on `(organization_id, user_id)` and single index on `user_id`.
