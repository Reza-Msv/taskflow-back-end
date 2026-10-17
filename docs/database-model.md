# Database Model Design & Architecture

## 1. Overview & Architecture
TaskFlow implements a multi-tenant relational persistence architecture powered by PostgreSQL, SQLAlchemy 2.0 (Async), and Alembic. The schema enforces strict tenant isolation, deterministic issue sequence numbering, optimistic concurrency control, and relational integrity via foreign keys with explicit cascade semantics.

All primary keys use UUIDs (UUIDv4/v7), and timestamps are stored with timezone awareness (`TIMESTAMPTZ` / UTC).

---

## 2. Implemented Schema Specification

### 2.1 Identity Domain (`taskflow.domains.identity`)

#### `users`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Unique identifier |
| `email` | `VARCHAR(255)` | Unique, Not Null, Index | User email address |
| `full_name` | `VARCHAR(255)` | Not Null | User full display name |
| `is_active` | `BOOLEAN` | Not Null, Default `true` | Account active status flag |
| `is_superuser` | `BOOLEAN` | Not Null, Default `false` | Superuser privileges flag |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Account creation timestamp (UTC) |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Last profile update timestamp (UTC) |

#### `user_credentials`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Unique identifier |
| `user_id` | `UUID` | FK `users(id)` ON DELETE CASCADE, Unique, Index | 1:1 link to owner user record |
| `password_hash` | `VARCHAR(255)` | Not Null | Argon2id password hash |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp (UTC) |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Last modification timestamp (UTC) |

#### `sessions`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Unique identifier |
| `user_id` | `UUID` | FK `users(id)` ON DELETE CASCADE, Not Null, Index | Owning user ID |
| `token_hash` | `VARCHAR(64)` | Unique, Not Null, Index | SHA-256 digest of session token |
| `csrf_token` | `VARCHAR(64)` | Not Null | Cryptographic CSRF validation token |
| `user_agent` | `VARCHAR(500)` | Nullable | Client User-Agent string |
| `ip_address` | `VARCHAR(45)` | Nullable | Client IP address (IPv4 / IPv6) |
| `expires_at` | `TIMESTAMPTZ` | Not Null, Index | Expiration timestamp (UTC) |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Session issue timestamp (UTC) |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Session update timestamp (UTC) |

---

### 2.2 Organizations Domain (`taskflow.domains.organizations`)

#### `organizations`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Unique tenant organization identifier |
| `name` | `VARCHAR(255)` | Not Null | Display name |
| `slug` | `VARCHAR(100)` | Unique, Not Null, Index | URL-safe identifier (e.g., `acme-corp`) |
| `is_active` | `BOOLEAN` | Not Null, Default `true` | Organization status |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Last update timestamp |

#### `organization_memberships`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Membership record ID |
| `organization_id` | `UUID` | FK `organizations(id)` ON DELETE CASCADE, Index | Tenant organization ID |
| `user_id` | `UUID` | FK `users(id)` ON DELETE CASCADE, Index | Member user ID |
| `role` | `VARCHAR(50)` | Not Null, Default `member` | Role: `owner`, `admin`, `member`, `guest`, `viewer` |
| `is_active` | `BOOLEAN` | Not Null, Default `true` | Membership status flag |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Joining timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Role change timestamp |
*Composite Constraint*: `uq_organization_memberships_org_user (organization_id, user_id)`

#### `invitations`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Unique invitation ID |
| `organization_id` | `UUID` | FK `organizations(id)` ON DELETE CASCADE, Index | Target organization |
| `email` | `VARCHAR(255)` | Not Null, Index | Invitee email |
| `role` | `VARCHAR(50)` | Not Null, Default `member` | Assigned role upon acceptance |
| `token` | `VARCHAR(255)` | Unique, Not Null, Index | Secure single-use invitation token |
| `invited_by_id` | `UUID` | FK `users(id)` ON DELETE SET NULL, Nullable | Inviting user |
| `expires_at` | `TIMESTAMPTZ` | Not Null | Token expiration timestamp |
| `accepted_at` | `TIMESTAMPTZ` | Nullable | Acceptance timestamp |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Issuance timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Update timestamp |

#### `teams`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Team ID |
| `organization_id` | `UUID` | FK `organizations(id)` ON DELETE CASCADE, Index | Owning organization |
| `name` | `VARCHAR(255)` | Not Null | Team display name |
| `description` | `TEXT` | Nullable | Optional team charter / description |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Update timestamp |

#### `team_memberships`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Team membership ID |
| `team_id` | `UUID` | FK `teams(id)` ON DELETE CASCADE, Index | Team ID |
| `user_id` | `UUID` | FK `users(id)` ON DELETE CASCADE, Index | User ID |
| `role` | `VARCHAR(50)` | Not Null, Default `member` | Team role: `lead`, `member` |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Update timestamp |
*Composite Constraint*: `uq_team_memberships_team_user (team_id, user_id)`

---

### 2.3 Projects Domain (`taskflow.domains.projects`)

#### `projects`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Project identifier |
| `organization_id` | `UUID` | FK `organizations(id)` ON DELETE CASCADE, Index | Tenant organization ID |
| `name` | `VARCHAR(255)` | Not Null | Project name |
| `key` | `VARCHAR(20)` | Not Null, Index | Project issue key prefix (e.g. `TF`, `WEB`) |
| `description` | `TEXT` | Nullable | Extended project description |
| `is_private` | `BOOLEAN` | Not Null, Default `false` | Access visibility |
| `is_archived` | `BOOLEAN` | Not Null, Default `false` | Soft archive flag |
| `owner_id` | `UUID` | FK `users(id)` ON DELETE SET NULL, Nullable | Project lead / owner |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Update timestamp |
*Composite Constraint*: `uq_projects_organization_key (organization_id, key)`

#### `project_memberships`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Membership ID |
| `project_id` | `UUID` | FK `projects(id)` ON DELETE CASCADE, Index | Project ID |
| `user_id` | `UUID` | FK `users(id)` ON DELETE CASCADE, Index | User ID |
| `role` | `VARCHAR(50)` | Not Null, Default `member` | Project role: `admin`, `member`, `viewer` |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Update timestamp |
*Composite Constraint*: `uq_project_memberships_project_user (project_id, user_id)`

#### `project_sequences`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Sequence tracker ID |
| `project_id` | `UUID` | FK `projects(id)` ON DELETE CASCADE, Unique, Index | Linked project |
| `current_sequence` | `INTEGER` | Not Null, Default `0` | High-water mark sequence number |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Last sequence allocation timestamp |
*Notes*: Incremented atomically inside `SELECT ... FOR UPDATE` transactions to guarantee collision-free, gapless issue keys.

---

### 2.4 Work Items Domain (`taskflow.domains.work_items`)

#### `work_items`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Work item identifier |
| `project_id` | `UUID` | FK `projects(id)` ON DELETE CASCADE, Index | Owning project |
| `organization_id` | `UUID` | FK `organizations(id)` ON DELETE CASCADE, Index | Tenant organization ID (IDOR defense) |
| `sequence_number` | `INTEGER` | Not Null | Sequence number within project (e.g. `101`) |
| `issue_key` | `VARCHAR(50)` | Not Null, Index | Human-readable key (e.g. `TF-101`) |
| `type` | `VARCHAR(50)` | Not Null, Default `task` | `task`, `bug`, `story`, `epic`, `subtask` |
| `title` | `VARCHAR(255)` | Not Null | Summary title |
| `description` | `TEXT` | Nullable | Full Markdown description |
| `status` | `VARCHAR(50)` | Not Null, Default `todo`, Index | Status state: `todo`, `in_progress`, `done`, etc. |
| `priority` | `VARCHAR(50)` | Not Null, Default `medium` | Priority: `lowest`, `low`, `medium`, `high`, `urgent` |
| `parent_id` | `UUID` | FK `work_items(id)` ON DELETE SET NULL, Nullable, Index | Parent item (for subtasks and epics) |
| `reporter_id` | `UUID` | FK `users(id)` ON DELETE SET NULL, Nullable, Index | Creating user |
| `assignee_id` | `UUID` | FK `users(id)` ON DELETE SET NULL, Nullable, Index | Responsible assignee |
| `sprint_id` | `UUID` | FK `sprints(id)` ON DELETE SET NULL, Nullable, Index | Assigned sprint |
| `rank` | `VARCHAR(255)` | Not Null, Default `0\|hzzzzz:`, Index | Lexicographical relative rank for backlog ordering |
| `due_date` | `TIMESTAMPTZ` | Nullable | Target due date |
| `estimated_points` | `FLOAT` | Nullable | Story points / complexity estimate |
| `version` | `INTEGER` | Not Null, Default `1` | Optimistic concurrency version lock |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Modification timestamp |
*Composite Constraints*:
- `uq_work_items_project_sequence (project_id, sequence_number)`
- `uq_work_items_project_issue_key (project_id, issue_key)`
- `ix_work_items_org_status (organization_id, status)`

#### `labels`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Label ID |
| `organization_id` | `UUID` | FK `organizations(id)` ON DELETE CASCADE, Index | Tenant organization |
| `name` | `VARCHAR(100)` | Not Null | Label text (e.g. `Frontend`, `Backend`) |
| `color` | `VARCHAR(20)` | Not Null, Default `#6B7280` | Hex color code |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Update timestamp |
*Composite Constraint*: `uq_labels_organization_name (organization_id, name)`

#### `work_item_labels` (M:N Association Table)
| Column | Type | Constraints | Description |
|---|---|---|---|
| `work_item_id` | `UUID` | PK, FK `work_items(id)` ON DELETE CASCADE | Work item ID |
| `label_id` | `UUID` | PK, FK `labels(id)` ON DELETE CASCADE | Label ID |

#### `work_item_activities`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Audit activity event ID |
| `work_item_id` | `UUID` | FK `work_items(id)` ON DELETE CASCADE, Index | Target work item |
| `user_id` | `UUID` | FK `users(id)` ON DELETE SET NULL, Nullable, Index | Actor who made the change |
| `field` | `VARCHAR(100)` | Not Null | Changed attribute name (e.g. `status`, `assignee`) |
| `old_value` | `TEXT` | Nullable | Value prior to change |
| `new_value` | `TEXT` | Nullable | Value following change |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Audit timestamp |

#### `attachments`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Attachment ID |
| `work_item_id` | `UUID` | FK `work_items(id)` ON DELETE CASCADE, Index | Attached work item |
| `uploader_id` | `UUID` | FK `users(id)` ON DELETE SET NULL, Nullable, Index | Uploading user |
| `file_name` | `VARCHAR(255)` | Not Null | Original file name |
| `file_size` | `BIGINT` | Not Null | Size in bytes |
| `content_type` | `VARCHAR(100)` | Not Null | MIME type |
| `storage_path` | `VARCHAR(500)` | Not Null | Storage path or bucket key |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Upload timestamp |

#### `workflow_statuses` & `workflow_transitions`
- `workflow_statuses`: Configurable workflow stages with categories (`todo`, `in_progress`, `done`, `cancelled`).
- `workflow_transitions`: Valid transition graph restricting allowed state movements.

---

### 2.5 Collaboration Domain (`taskflow.domains.collaboration`)

#### `work_item_comments`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Comment ID |
| `work_item_id` | `UUID` | FK `work_items(id)` ON DELETE CASCADE, Index | Target work item |
| `author_id` | `UUID` | FK `users(id)` ON DELETE SET NULL, Nullable, Index | Comment author |
| `content` | `TEXT` | Not Null | Markdown comment content |
| `is_edited` | `BOOLEAN` | Not Null, Default `false` | Edited status indicator |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Modification timestamp |

#### `notifications`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Notification ID |
| `user_id` | `UUID` | FK `users(id)` ON DELETE CASCADE, Index | Recipient user |
| `actor_id` | `UUID` | FK `users(id)` ON DELETE SET NULL, Nullable, Index | Event originator user |
| `organization_id` | `UUID` | FK `organizations(id)` ON DELETE CASCADE, Nullable | Tenant context |
| `type` | `VARCHAR(100)` | Not Null | Notification category (`comment_added`, `assigned`, etc.) |
| `title` | `VARCHAR(255)` | Not Null | Short notification headline |
| `message` | `TEXT` | Not Null | Detailed notification body |
| `resource_type` | `VARCHAR(50)` | Nullable | Referenced entity (`work_item`, `sprint`) |
| `resource_id` | `UUID` | Nullable | Referenced entity ID |
| `is_read` | `BOOLEAN` | Not Null, Default `false`, Index | Read receipt flag |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Notification timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Read timestamp |

---

### 2.6 Scrum Domain (`taskflow.domains.scrum`)

#### `sprints`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Sprint ID |
| `project_id` | `UUID` | FK `projects(id)` ON DELETE CASCADE, Index | Project ID |
| `name` | `VARCHAR(255)` | Not Null | Sprint title (e.g. `Sprint 24`) |
| `goal` | `TEXT` | Nullable | Sprint goal |
| `status` | `VARCHAR(50)` | Not Null, Default `draft` | Status: `draft`, `active`, `completed` |
| `start_date` | `TIMESTAMPTZ` | Nullable | Official sprint start |
| `end_date` | `TIMESTAMPTZ` | Nullable | Planned completion date |
| `completed_at` | `TIMESTAMPTZ` | Nullable | Actual closure timestamp |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Update timestamp |
*Composite Index*: `ix_sprints_project_status (project_id, status)`

#### `sprint_snapshots`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Snapshot ID |
| `sprint_id` | `UUID` | FK `sprints(id)` ON DELETE CASCADE, Index | Sprint ID |
| `snapshot_type` | `VARCHAR(50)` | Not Null | Snapshot milestone: `start`, `end`, `daily` |
| `total_points` | `FLOAT` | Not Null, Default `0.0` | Total committed story points |
| `completed_points` | `FLOAT` | Not Null, Default `0.0` | Completed story points |
| `total_items` | `INTEGER` | Not Null, Default `0` | Total work items in sprint |
| `completed_items` | `INTEGER` | Not Null, Default `0` | Completed work items |
| `scope_added_items` | `INTEGER` | Not Null, Default `0` | Items added post sprint start (churn) |
| `scope_removed_items`| `INTEGER` | Not Null, Default `0` | Items removed post sprint start |
| `recorded_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Snapshot recording timestamp |

#### `sprint_retrospectives`
| Column | Type | Constraints | Description |
|---|---|---|---|
| `id` | `UUID` | PK, Not Null | Retrospective ID |
| `sprint_id` | `UUID` | FK `sprints(id)` ON DELETE CASCADE, Unique, Index | Linked sprint |
| `what_went_well` | `TEXT` | Nullable | Positive team feedback |
| `what_could_be_improved` | `TEXT` | Nullable | Improvement opportunities |
| `action_items` | `TEXT` | Nullable | Concrete corrective actions |
| `created_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Creation timestamp |
| `updated_at` | `TIMESTAMPTZ` | Not Null, Default `now()` | Update timestamp |

---

## 3. Alembic Migration History
1. `20261017_0001`: `create identity tables` (`users`, `user_credentials`, `sessions`).
2. `20261017_0002`: `create taskflow core schema` (`organizations`, `organization_memberships`, `invitations`, `teams`, `team_memberships`, `projects`, `project_memberships`, `project_sequences`, `sprints`, `sprint_snapshots`, `sprint_retrospectives`, `workflow_statuses`, `workflow_transitions`, `labels`, `work_items`, `work_item_labels`, `work_item_activities`, `attachments`, `work_item_comments`, `notifications`).
