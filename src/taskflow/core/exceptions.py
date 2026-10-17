from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse


class DomainException(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


async def domain_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    domain_exc = (
        exc
        if isinstance(exc, DomainException)
        else DomainException("INTERNAL_ERROR", str(exc), 500)
    )
    request_id = getattr(request.state, "request_id", "req_unknown")
    return JSONResponse(
        status_code=domain_exc.status_code,
        content={
            "error": {
                "code": domain_exc.code,
                "message": domain_exc.message,
                "details": domain_exc.details,
                "request_id": request_id,
            }
        },
    )


async def validation_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "req_unknown")
    errors = getattr(exc, "errors", lambda: str(exc))()
    return JSONResponse(
        status_code=getattr(
            status,
            "HTTP_422_UNPROCESSABLE_CONTENT",
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ),
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"errors": errors},
                "request_id": request_id,
            }
        },
    )
