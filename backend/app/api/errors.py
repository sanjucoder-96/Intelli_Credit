"""Exception handlers producing the canonical error envelope.

Every non-2xx response is ``{"error": {code, message, details, request_id}}``
(``app.api.schemas.common.ErrorResponse``). Unhandled exceptions are logged
with a stack trace and answered with an opaque 500 — internals never leak to
clients. Later phases only add :class:`AppError` subclasses; the shape here
is stable.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.api.schemas.common import ErrorDetail, ErrorResponse
from app.core.context import current_request_id
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger("app.api.errors")


def _envelope(
    *,
    code: str,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body = ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            details=details,
            request_id=current_request_id.get(),
        )
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(mode="json"))


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    """Translate an expected :class:`AppError` into its envelope."""
    assert isinstance(exc, AppError)  # registered for AppError only
    return _envelope(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
    )


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Translate request-schema validation failures into a 422 envelope."""
    assert isinstance(exc, RequestValidationError)  # registered for it only
    return _envelope(
        code="validation_failed",
        message="Request validation failed",
        status_code=422,
        details={"errors": jsonable_encoder(exc.errors())},
    )


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    """Log an unhandled exception and answer with an opaque 500 envelope."""
    logger.exception("unhandled_exception", method=request.method, path=request.url.path)
    return _envelope(
        code="internal_error",
        message="An internal error occurred",
        status_code=500,
    )


def register_error_handlers(app: FastAPI) -> None:
    """Attach the canonical envelope handlers to the application."""
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected_error)
