"""Request-context middleware: correlation ID + one structured access log.

Accepts an inbound ``X-Request-ID`` (gateway-issued) or generates a UUIDv4,
binds it to structlog contextvars and :data:`current_request_id`, echoes it on
the response, and emits exactly one ``request_completed`` line per request.
Phase 4 propagates the same ID into Celery task headers.
"""

from __future__ import annotations

import time
from uuid import uuid4

import structlog
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import current_request_id
from app.core.logging import bind_request_context, get_logger

_HEADER = "X-Request-ID"

logger = get_logger("app.api.request")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Bind per-request correlation context and log request completion.

    The contextvar is deliberately not reset here: exception handlers running
    outside this middleware (the 500 envelope) still need the request ID, and
    each request task gets a fresh context copy anyway —
    ``clear_contextvars`` at entry removes any residue.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get(_HEADER) or str(uuid4())
        structlog.contextvars.clear_contextvars()
        bind_request_context(request_id)
        current_request_id.set(request_id)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = (time.perf_counter() - started) * 1000
            logger.info(
                "request_failed",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration_ms, 2),
            )
            raise

        duration_ms = (time.perf_counter() - started) * 1000
        response.headers[_HEADER] = request_id
        logger.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=round(duration_ms, 2),
        )
        return response
