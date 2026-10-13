from fastapi import APIRouter

from taskflow.api.v1.endpoints.health import HealthResponse
from taskflow.core.config import settings

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["System"])
async def health_check() -> HealthResponse:
    """Liveness probe verifying that the API server is operational."""
    return HealthResponse(
        status="ok",
        version="0.1.0",
        environment=settings.ENVIRONMENT,
    )
