# Architecture Principles

1. **Modular Monolith**: One deployable FastAPI service. Clear domain packages under `src/taskflow/domains/`.
2. **Domain Layering**:
   - `models.py`: SQLAlchemy ORM classes.
   - `schemas.py`: Pydantic request and response schemas (DTOs).
   - `repository.py`: Database query building and persistence operations.
   - `service.py`: Transaction boundaries, authorization policy application, business rules.
3. **Endpoint Layering**:
   - `src/taskflow/api/v1/endpoints/`: Thin controllers only. No business logic or direct raw SQL.
4. **Deterministic Workflows**:
   - Status changes must follow configured state transitions.
   - Issue keys generated atomically using dedicated sequence tables (`WEB-1`, `WEB-2`).
