# Backend Architecture

## Modular Monolith Overview
`taskflow-back-end` is a modular monolith exposing a versioned REST API. All domain logic resides in dedicated packages:

```
src/taskflow/
├── core/         # Cross-cutting concerns: config, security, exceptions, logging
├── db/           # Database engine, session maker, declarative base, types
├── api/          # FastAPI routers and endpoint controllers
└── domains/      # Business domains: identity, organizations, projects, work_items, scrum
```

## Request Lifecycle
1. **HTTP Request** arrives at FastAPI route in `api/v1/endpoints/`.
2. **Dependency Injection** (`api/deps.py`) authenticates the token/session and resolves the tenant context.
3. **Pydantic Validation** validates the request payload.
4. **Domain Service** enforces business rules, access policies, and defines the transaction boundary.
5. **Domain Repository** issues SQLAlchemy async queries against PostgreSQL.
6. **Response DTO** serializes the domain result into JSON.
