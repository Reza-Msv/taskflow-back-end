# Product Requirements Document (PRD) — TaskFlow Backend API
**Service:** `taskflow-back-end`  
**Architecture:** Python 3.12+, FastAPI, SQLAlchemy 2.x (Async), Pydantic v2, Alembic, PostgreSQL  
**Version:** 1.0.0-draft  
**AI Dependency:** Strictly Out of Scope (Deterministic software only)

---

## 1. Executive Summary & Purpose
The `taskflow-back-end` service is the authoritative source of truth for the TaskFlow platform. It provides RESTful APIs, business transaction boundaries, tenant and role-based authorization, database persistence, and OpenAPI contract generation.

### Non-Negotiable Core Tenets:
1. **Contract-First Source of Truth**: Backend Pydantic DTOs define the API contracts. OpenAPI schemas are exported for frontend type generation.
2. **Backend Authoritative Authorization**: Permission checks happen strictly in API dependencies and domain services, never assuming client compliance.
3. **Multi-Tenant Isolation**: Every organization-scoped query and mutation is partitioned by `organization_id` with active membership checks.
4. **Deterministic Workflows**: All state transitions, issue key numbering, backlog ranking, and sprint lifecycles are deterministic and auditable.

---

## 2. Core Functional Modules

### 2.1 Identity & Access Management (`domains/identity`)
- Secure password authentication using **Argon2id**.
- Session-based or short-lived token authentication with secure cookie or bearer handling.
- Account profile (`/api/v1/users/me`), session revocation, and password management.
- Tenant context resolution: verifying actor belongs to active organization.

### 2.2 Organizations & Membership (`domains/organizations`)
- Organization creation and lifecycle settings.
- Organization roles: `owner`, `admin`, `member`, `guest`, `viewer`.
- Secure single-use invitation tokens with expirations.
- Protection against deleting the last active owner.

### 2.3 Projects & Sequences (`domains/projects`)
- Project keys (e.g. `WEB`, `PAY`), unique within an organization.
- Transaction-safe issue sequence counters (`project_sequences`) to eliminate race conditions on issue keys.
- Project membership and privacy settings (private vs organization-wide).
- Soft archive and restore capabilities.

### 2.4 Work Items & Workflows (`domains/work_items`)
- Core polymorphic work items: `task`, `bug`, `story`, `epic`, `subtask`.
- Status categories: `todo`, `in_progress`, `done`, `cancelled`.
- Backend transition rules and workflow policy validation.
- Optimistic concurrency control via integer `version` field (returns `409 Conflict` on stale writes).
- Activity stream recording (who changed what, previous vs new values, timestamps).
- Comment threads with Markdown format.

### 2.5 Scrum & Sprint Management (`domains/scrum`)
- Backlog management with relative item ranking.
- Sprint lifecycles: `draft` -> `active` -> `completed`.
- Constraint: Only 1 active sprint per project (unless multi-team mode configured).
- Sprint scope snapshots upon sprint start to track scope churn.
- Sprint completion policy: configurable handling of incomplete items (return to backlog or carry to next sprint).
- Burndown calculation and committed vs completed velocity reports.

---

## 3. Data & Persistence Model
- **Primary Keys**: UUIDv7 / UUIDv4.
- **Timestamps**: All timestamps stored as timezone-aware UTC (`timestamptz`).
- **Migrations**: Managed strictly via Alembic. No automatic `Base.metadata.create_all()` in production.
- **Query Hygiene**: Eager-load relationships explicitly via `selectinload` or `joinedload` to prevent N+1 queries.

---

## 4. API & Error Conventions
Standard error schema returned on all non-2xx responses:
```json
{
  "error": {
    "code": "WORK_ITEM_NOT_FOUND",
    "message": "The requested work item WEB-12 does not exist or is inaccessible.",
    "details": {},
    "request_id": "req_01j9..."
  }
}
```

HTTP Status mapping:
- `400`: Malformed payload or invalid business operation.
- `401`: Unauthenticated.
- `403`: Authenticated, but lacking permission.
- `404`: Resource not found or concealed due to privacy.
- `409`: Optimistic lock conflict or conflicting state.
- `422`: Schema validation failure.
- `500`: Unhandled server exception.

---

## 5. Security & Isolation Requirements
- Passwords hashed with Argon2id (memory cost 64MB, 3 iterations, 4 parallelism).
- Strict IDOR defense: every lookup includes `organization_id` filter and user membership validation.
- Sanitized input and parameterized SQL queries exclusively through SQLAlchemy.
- Rate limiting on authentication and search endpoints.
