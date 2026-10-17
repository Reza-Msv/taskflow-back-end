import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from taskflow.api.v1.router import router as api_v1_router
from taskflow.core.config import settings
from taskflow.core.exceptions import (
    DomainException,
    domain_exception_handler,
    validation_exception_handler,
)


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS configuration
    if settings.BACKEND_CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.BACKEND_CORS_ORIGINS,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Request ID middleware
    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        req_id = request.headers.get("X-Request-ID", f"req_{uuid.uuid4().hex[:12]}")
        request.state.request_id = req_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response

    # Exception Handlers
    app.add_exception_handler(DomainException, domain_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    # Mount API routers
    app.include_router(api_v1_router, prefix=settings.API_V1_STR)

    # Root liveness check
    @app.get("/health", tags=["System"])
    async def root_health():
        return {"status": "ok", "service": "taskflow-api"}

    return app


app = create_app()
