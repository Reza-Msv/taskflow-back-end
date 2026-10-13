# Backend Conventions

1. **Python & Typing**:
   - Python 3.12+ features (pipe union `A | B`, modern type annotations).
   - Strict type hints throughout services and repositories. No unannotated functions.
2. **FastAPI & Routers**:
   - Route paths use lowercase kebab-case for resources: `/work-items`, `/sprints`.
   - Plural resource endpoints: `GET /projects`, `POST /projects`.
   - Nested resources express real relationships: `GET /projects/{project_id}/sprints`.
3. **Data Transfer Objects (DTOs)**:
   - Request DTOs: `CreateWorkItemRequest`, `UpdateWorkItemRequest`.
   - Response DTOs: `WorkItemResponse`, `WorkItemListResponse`.
   - Use `model_config = ConfigDict(from_attributes=True)` in response DTOs.
4. **Error Responses**:
   - Always raise domain exceptions that map to HTTP status codes with machine-readable error codes.
