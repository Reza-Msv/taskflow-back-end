# Backend Architecture

## Modular Monolith Overview
`taskflow-back-end` is a modular monolith exposing a versioned REST API. All domain logic resides in dedicated packages:

```
src/taskflow/
├── core/         # Cross-cutting concerns: config, security, exceptions, logging
├── db/           # Database engine, session maker, declarative base, types
├── api/          # FastAPI routers, dependencies (deps.py), and endpoint controllers
└── domains/      # Business domains:
    ├── identity/      # Implemented: User, Credentials, Session, AuthService, IAM DTOs
    ├── organizations/ # Organizations, Memberships, Invitations, Teams
    ├── projects/      # Projects, Sequences, Project Memberships
    ├── work_items/    # Work Items, Workflow Transitions, Comments, Activity
    └── scrum/         # Sprints, Backlog, Scope Events, Retrospectives
```

## Request Lifecycle
1. **HTTP Request** arrives at FastAPI route in `api/v1/endpoints/` (e.g. `auth.py`).
2. **Dependency Injection** (`api/deps.py`) authenticates the token/session (from HttpOnly cookie or Bearer header), enforces CSRF checks for mutating cookie requests, and resolves user/tenant context.
3. **Pydantic Validation** validates the request payload and headers. Validation failures are intercepted by `validation_exception_handler` returning standardized JSON.
4. **Domain Service** (e.g. `AuthService`) enforces business rules, access policies, password hashing, and defines the transaction boundary.
5. **Domain Repository** (e.g. `UserRepository`, `SessionRepository`) issues SQLAlchemy async queries against PostgreSQL.
6. **Response DTO** serializes the domain result into JSON (`UserResponse`, `AuthResponse`).
