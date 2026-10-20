from fastapi import APIRouter

from taskflow.api.v1.endpoints.admin import router as admin_router
from taskflow.api.v1.endpoints.auth import router as auth_router
from taskflow.api.v1.endpoints.dashboard import router as dashboard_router
from taskflow.api.v1.endpoints.health import HealthResponse
from taskflow.api.v1.endpoints.my_tasks import router as my_tasks_router
from taskflow.api.v1.endpoints.notifications import (
    router as notifications_router,
)
from taskflow.api.v1.endpoints.notifications import (
    websocket_notifications,
)
from taskflow.api.v1.endpoints.organizations import router as organizations_router
from taskflow.api.v1.endpoints.projects import router as projects_router
from taskflow.api.v1.endpoints.saved_filters import (
    router as saved_filters_router,
)
from taskflow.api.v1.endpoints.search import router as search_router
from taskflow.api.v1.endpoints.tasks import router as tasks_router
from taskflow.core.config import settings

router = APIRouter()
router.include_router(auth_router)
router.include_router(admin_router)
router.include_router(dashboard_router)
router.include_router(my_tasks_router)
router.include_router(organizations_router)
router.include_router(projects_router)
router.include_router(tasks_router)
router.include_router(notifications_router)
router.include_router(search_router)
router.include_router(saved_filters_router)
router.add_api_websocket_route(
    "/ws/notifications", websocket_notifications, name="ws_notifications_alias"
)



@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """Liveness probe verifying that the API server is operational."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        environment=settings.ENVIRONMENT,
    )
