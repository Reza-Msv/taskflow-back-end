from fastapi import APIRouter

from taskflow.api.v1.endpoints.admin import router as admin_router
from taskflow.api.v1.endpoints.auth import router as auth_router
from taskflow.api.v1.endpoints.health import HealthResponse
from taskflow.core.config import settings

router = APIRouter()
router.include_router(auth_router)
router.include_router(admin_router)


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """Liveness probe verifying that the API server is operational."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        environment=settings.ENVIRONMENT,
    )
