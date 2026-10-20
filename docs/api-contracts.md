# API Contracts & Conventions

## Source of Truth
FastAPI schemas are exported as OpenAPI 3.1 specification at:
- JSON: `/api/v1/openapi.json`
- Interactive Swagger: `/docs`
- ReDoc: `/redoc`

The frontend consumes this schema directly using `openapi-typescript` to ensure zero drift.

## Standard Error Format
All errors (4xx, 5xx) return:
```json
{
  "error": {
    "code": "ERROR_MACHINE_NAME",
    "message": "Human readable, safe error message",
    "details": {},
    "request_id": "req_01j9a..."
  }
}
```

Standard Error Codes:
- `VALIDATION_ERROR` (422): Schema/payload validation failure.
- `INVALID_CREDENTIALS` (401): Incorrect email or password.
- `UNAUTHORIZED` (401): Missing, malformed, or expired session.
- `CSRF_TOKEN_INVALID` (403): Missing or invalid `X-CSRF-Token` header for cookie-authenticated mutating requests.
- `USER_INACTIVE` (403): User account has been deactivated.
- `REGISTRATION_DISABLED` (403): Public registration is disabled by policy.
- `EMAIL_ALREADY_EXISTS` (409): Account with provided email already registered.
- `INTERNAL_ERROR` (500): Unexpected server failure.

---

## Authentication & Session API Contracts

### Overview & Security Model
- **Primary Auth Mode**: Secure, `HttpOnly`, `SameSite=Lax` cookie (`taskflow_session`).
- **CSRF Protection**: For browser clients using cookies, mutating endpoints (`POST`, `PUT`, `PATCH`, `DELETE`) require the `X-CSRF-Token` header matching the session CSRF token. The CSRF token is provided in the JSON login/register response body and as a client-readable cookie (`taskflow_csrf`).
- **Alternative Auth Mode**: Bearer token header (`Authorization: Bearer <token>`). When Bearer tokens are used, CSRF header checks are omitted (safe per OWASP guidance).
- **CORS Requirements**: Frontend requests from `http://localhost:3000` must specify `credentials: "include"` (Fetch API) or `withCredentials: true` (Axios) so session cookies are included in requests.

---

### 1. User Registration
`POST /api/v1/auth/register`

Creates a new user and hashes password with Argon2id. Registration does not create an active session, does not issue cookies, and does not automatically log the user in. The client redirects the user to the login endpoint to authenticate.

**Request Headers**:
- `Content-Type: application/json`

**Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!",
  "full_name": "Alice Smith"
}
```
*Validation Rules*:
- `email`: Valid email format.
- `password`: String, min 8 characters, max 128 characters.
- `full_name`: String, min 1 character, max 255 characters.

**Success Response**: `201 Created`
- **Cookies Set**: None
- **Response Body**:
```json
{
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "full_name": "Alice Smith",
    "is_active": true,
    "is_superuser": false,
    "created_at": "2026-10-17T07:30:00Z",
    "updated_at": "2026-10-17T07:30:00Z"
  },
  "message": "User registered successfully"
}
```

**Errors**:
- `409 Conflict`: `EMAIL_ALREADY_EXISTS`
- `403 Forbidden`: `REGISTRATION_DISABLED` (if `ENABLE_PUBLIC_REGISTRATION=false`)
- `422 Unprocessable Content`: `VALIDATION_ERROR`

---

### 2. User Login
`POST /api/v1/auth/login`

Authenticates credentials, generates a new session, and sets session cookies.

**Request Body**:
```json
{
  "email": "user@example.com",
  "password": "SecurePassword123!"
}
```

**Success Response**: `200 OK`
- **Cookies Set**:
  - `taskflow_session`: `<session_token>` (`HttpOnly; Path=/; SameSite=Lax; Max-Age=604800`)
  - `taskflow_csrf`: `<csrf_token>` (`Path=/; SameSite=Lax; Max-Age=604800`)
- **Response Body**:
```json
{
  "user": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "full_name": "Alice Smith",
    "is_active": true,
    "is_superuser": false,
    "created_at": "2026-10-17T07:30:00Z",
    "updated_at": "2026-10-17T07:30:00Z"
  },
  "csrf_token": "a1b2c3d4e5f60718...",
  "message": "Authenticated successfully"
}
```

**Errors**:
- `401 Unauthorized`: `INVALID_CREDENTIALS` (email not found or incorrect password)
- `403 Forbidden`: `USER_INACTIVE`
- `422 Unprocessable Content`: `VALIDATION_ERROR`

---

### 3. User Logout
`POST /api/v1/auth/logout`

Revokes the active session in PostgreSQL and clears cookies on the client.

**Authentication & CSRF Requirements**:
- Cookie Auth:
  - Must include `taskflow_session` cookie.
  - Must include `X-CSRF-Token: <csrf_token>` header.
- Bearer Auth:
  - Must include `Authorization: Bearer <session_token>` header (CSRF header optional).

**Success Response**: `200 OK`
- **Cookies Deleted**:
  - `taskflow_session` (expired/deleted)
  - `taskflow_csrf` (expired/deleted)
- **Response Body**:
```json
{
  "message": "Successfully logged out"
}
```

**Errors**:
- `401 Unauthorized`: `UNAUTHORIZED`
- `403 Forbidden`: `CSRF_TOKEN_INVALID`

---

### 4. Current User Profile
`GET /api/v1/auth/me`

Retrieves the authenticated user profile.

**Authentication Requirements**:
- `taskflow_session` cookie OR `Authorization: Bearer <session_token>` header.

**Success Response**: `200 OK`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "email": "user@example.com",
  "full_name": "Alice Smith",
  "is_active": true,
  "is_superuser": false,
  "created_at": "2026-10-17T07:30:00Z",
  "updated_at": "2026-10-17T07:30:00Z"
}
```

**Errors**:
- `401 Unauthorized`: `UNAUTHORIZED` (missing, invalid, or expired session)
- `403 Forbidden`: `USER_INACTIVE`

---

## Next.js Frontend Integration Example

```typescript
// Example fetch utility with CSRF header injection and credential inclusion
async function apiRequest<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  // Extract CSRF token from document.cookie
  const csrfToken = document.cookie
    .split("; ")
    .find((row) => row.startsWith("taskflow_csrf="))
    ?.split("=")[1];

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };

  if (csrfToken && options.method && ["POST", "PUT", "PATCH", "DELETE"].includes(options.method.toUpperCase())) {
    headers["X-CSRF-Token"] = csrfToken;
  }

  const response = await fetch(`http://localhost:8000${endpoint}`, {
    ...options,
    headers,
    credentials: "include", // Essential for HttpOnly session cookie transmission
  });

  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error?.message || "API request failed");
  }
  return data;
}
```

---

## Pagination Schema
Offset-based pagination:
```json
{
  "items": [],
  "page": 1,
  "page_size": 25,
  "total": 100
}
```
Cursor-based pagination (for high-volume feeds):
```json
{
  "items": [],
  "next_cursor": "eyJpZCI6...",
  "has_more": true
}
```

---

## Phase 4: Task Details & Collaboration API Contracts

### 1. Task Details Aggregation
`GET /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}`

Returns the comprehensive task detail representation (`WorkItemDetailResponse`) including parent context, project context, assignees, subtasks, checklists, attachments, comments count, and checklists completion progress.

**Success Response**: `200 OK`
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "project_id": "550e8400-e29b-41d4-a716-446655440001",
  "organization_id": "550e8400-e29b-41d4-a716-446655440002",
  "parent_id": null,
  "issue_key": "ENG-101",
  "sequence_number": 101,
  "title": "Implement OAuth2 login flow",
  "description": "Full description in Markdown",
  "type": "task",
  "status": "in_progress",
  "priority": "high",
  "assignee_id": "550e8400-e29b-41d4-a716-446655440003",
  "reporter_id": "550e8400-e29b-41d4-a716-446655440004",
  "due_date": "2026-11-01T00:00:00Z",
  "estimated_points": 5.0,
  "version": 2,
  "subtasks": [],
  "checklists": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440005",
      "work_item_id": "550e8400-e29b-41d4-a716-446655440000",
      "title": "Setup OAuth client secrets",
      "is_completed": true,
      "position": 0,
      "assignee_id": null,
      "assignee": null,
      "created_at": "2026-10-20T10:00:00Z",
      "updated_at": "2026-10-20T10:05:00Z"
    }
  ],
  "attachments": [],
  "comments_count": 3,
  "checklists_total": 1,
  "checklists_completed": 1,
  "subtasks_total": 0,
  "subtasks_completed": 0,
  "created_at": "2026-10-20T09:00:00Z",
  "updated_at": "2026-10-20T10:05:00Z"
}
```

---

### 2. Task Comments & Mentions
- `POST /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments`
  - Body: `{"content": "Working on this @[Alice](uuid)", "mentioned_user_ids": ["uuid"]}`
  - Dispatches `task_mentioned` notifications to mentioned eligible users and `task_comment` to assignees/reporters.
- `GET /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments?page=1&page_size=50`
  - Returns paginated comments with authors and mentioned users.
- `PATCH /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments/{comment_id}`
  - Author or Project/Org Admin only. Updates `content` and syncs `mentioned_user_ids`. Sets `is_edited=true`.
- `DELETE /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/comments/{comment_id}`
  - Author or Project/Org Admin only. Soft or hard cascade removes comment and records activity.
- `GET /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/mentionable-members?search=`
  - Returns active project/organization members eligible for `@` autocomplete.

---

### 3. Attachments & Storage Configuration
- `POST /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments`
  - Form multipart upload: `file: UploadFile`.
  - Max file size: 10 MB (configurable via `ATTACHMENT_MAX_FILE_SIZE_BYTES`).
  - Allowed extensions: `.png`, `.jpg`, `.jpeg`, `.gif`, `.webp`, `.svg`, `.pdf`, `.txt`, `.csv`, `.md`, `.json`, `.zip`.
  - Path traversal defense: Stored in sanitized tenant paths (`data/attachments/{org_id}/{project_id}/...`) with containment assertions.
- `GET /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments`
  - List metadata for all attachments on the task.
- `GET /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments/{attachment_id}/download`
  - Safely streams file with proper `Content-Type` and `Content-Disposition`.
- `DELETE /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/attachments/{attachment_id}`
  - Uploader or Admin only. Removes physical file and database row.

---

### 4. Checklists & Subtasks
- `POST /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists`
  - Body: `{"title": "Verify staging deploy", "position": 0, "assignee_id": null}`
- `GET /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists`
  - Ordered by position.
- `PATCH /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists/{item_id}`
  - Body: `{"title": "...", "is_completed": true, "position": 1, "assignee_id": "..."}`
- `DELETE /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/checklists/{item_id}`
- `POST /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/subtasks`
  - Body: `SubtaskCreateRequest` (`title`, `description`, `priority`, `assignee_id`, `due_date`, `estimated_points`).
  - Validates hierarchy: same project and organization, no self-parenting, cycle detection.
- `GET /api/v1/organizations/{org_id}/projects/{project_id}/tasks/{task_id}/subtasks?page=1&page_size=50`

---

### 5. In-App Notifications
- `GET /api/v1/notifications?unread_only=false&page=1&page_size=20`
  - Returns authenticated user's notifications.
- `GET /api/v1/notifications/unread-count`
  - Returns `{"unread_count": 3}` for badge display.
- `PATCH /api/v1/notifications/{notification_id}/read`
  - Marks notification as read.
- `POST /api/v1/notifications/read-all`
  - Marks all unread notifications for current user as read.
- `GET (WebSocket) /api/v1/notifications/ws` (Alias: `/api/v1/ws/notifications`)
  - Authenticated WebSocket endpoint for real-time user notification streaming.
  - Authenticates via HttpOnly `taskflow_session` cookie or `Authorization: Bearer <token>` handshake header. Never exposes tokens in URLs.
  - Rejects unauthenticated connections with close code `1008`.
  - Dispatches `connected`, `notification_created`, `notification_read`, `notifications_all_read`, and `pong`.
  - See full specification in [websocket-notifications.md](file:///Users/reza/Desktop/Task-Flow/back-end/taskflow-back-end/docs/websocket-notifications.md).


