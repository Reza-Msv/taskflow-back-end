# Backend Agent Operating Guidelines

This repository is maintained and developed with AI-assisted pair programming and multi-agent coordination. All contributors and agents must adhere strictly to these rules.

## Operating Principles
1. **Single Source of Truth**: Backend Pydantic schemas in `src/taskflow/domains/*/schemas.py` and `src/taskflow/api/` define the OpenAPI schema. Do not change schemas without reviewing impact on frontend consumers.
2. **Modular Monolith Boundaries**: Code is organized into domain packages under `src/taskflow/domains/`. Cross-domain calls must happen via clean service interfaces or domain events, not direct cross-domain table updates.
3. **No Direct DB mutations in Endpoints**: FastAPI endpoints are thin controllers. They parse requests, inject dependencies (current user, DB session), delegate to domain services, and return DTO responses.
4. **Zero AI Features**: AI, LLMs, or autonomous decision agents inside the application code are explicitly prohibited by project specification.

## Rules Reference
- [Architecture Principles](file://./rules/01-architecture-principles.md)
- [Backend Conventions](file://./rules/02-backend-conventions.md)
- [Security & Authorization](file://./rules/03-security-and-authorization.md)
- [Database & Migrations](file://./rules/04-database-and-migrations.md)
