"""Application error hierarchy mapped to the HTTP error envelope.

Raised anywhere in the stack and translated to the canonical
``{"error": {code, message, details, request_id}}`` envelope by the handlers
in ``app.api.errors``. Later phases add domain-specific subclasses (e.g.
Phase 4 ingestion errors, Phase 6 scoring errors) without changing the shape.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for expected, client-visible application errors."""

    code: str = "app_error"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details


class NotFoundError(AppError):
    """Requested resource does not exist (or is out of tenant scope)."""

    code = "not_found"
    status_code = 404


class UnauthorizedError(AppError):
    """Missing, invalid, or expired credentials."""

    code = "unauthorized"
    status_code = 401


class ForbiddenError(AppError):
    """Authenticated but not allowed to perform the action."""

    code = "forbidden"
    status_code = 403


class ConflictError(AppError):
    """State conflict (duplicate key, stale version, budget exhaustion)."""

    code = "conflict"
    status_code = 409


class ValidationFailedError(AppError):
    """Semantic validation failure beyond request-schema validation."""

    code = "validation_failed"
    status_code = 422


class ServiceUnavailableError(AppError):
    """A required downstream dependency is unreachable."""

    code = "service_unavailable"
    status_code = 503
