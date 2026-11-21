"""Shared response envelopes: errors, health, and readiness.

The error envelope shape ``{"error": {code, message, details, request_id}}``
is canonical for every non-2xx response (see ``app.api.errors``) and is what
the frontend API client unwraps. Stable from Phase 2 onward.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Machine-readable error payload inside the envelope."""

    code: str
    message: str
    details: dict[str, Any] | None = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    """Canonical error envelope returned for every non-2xx response."""

    error: ErrorDetail


class HealthResponse(BaseModel):
    """Liveness payload — static process identity, no I/O behind it."""

    status: str
    version: str
    environment: str


class ReadyResponse(BaseModel):
    """Readiness payload with one boolean per downstream dependency."""

    status: str
    checks: dict[str, bool]
