"""Custom exceptions and FastAPI exception handlers."""
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """Base class for application-defined errors.

    Subclasses set class-level code / http_status / message. Throwers may
    override message and attach details.
    """

    code: str = "internal_error"
    http_status: int = 500
    message: str = "Internal error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    code = "not_found"
    http_status = 404
    message = "Resource not found"


class UnauthorizedError(AppError):
    code = "unauthenticated"
    http_status = 401
    message = "Authentication required"


class ForbiddenError(AppError):
    code = "forbidden"
    http_status = 403
    message = "Permission denied"


class ConflictError(AppError):
    code = "conflict"
    http_status = 409
    message = "Conflict"


class ValidationFailedError(AppError):
    code = "validation_error"
    http_status = 400
    message = "Validation failed"


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _error_payload(
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    body = ErrorBody(
        code=code, message=message, details=details, request_id=request_id
    ).model_dump(exclude_none=True)
    return {"error": body}


def register_exception_handlers(app: FastAPI) -> None:
    """Register global handlers for AppError, HTTPException, validation, and unhandled."""

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
        logger.warning(
            "app.error",
            code=exc.code,
            status=exc.http_status,
            path=request.url.path,
            message=exc.message,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=_error_payload(
                exc.code, exc.message, exc.details or None, _request_id(request)
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_payload(
                code=f"http_{exc.status_code}",
                message=str(exc.detail),
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_error_payload(
                code="validation_error",
                message="Request validation failed",
                details={"errors": exc.errors()},
                request_id=_request_id(request),
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "app.unhandled_exception",
            path=request.url.path,
            exc_type=type(exc).__name__,
        )
        return JSONResponse(
            status_code=500,
            content=_error_payload(
                code="internal_error",
                message="Internal server error",
                request_id=_request_id(request),
            ),
        )
