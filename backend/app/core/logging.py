"""Structured logging via structlog with a stdlib bridge.

JSON logs to stdout in production, pretty console output in development
(ARCHITECTURE §14). Uvicorn, Celery, and any stdlib loggers are routed
through the same structlog processor chain so every line carries the bound
request/tenant context. Phase 4 extends this with Celery task correlation-ID
propagation; Phase 6 adds trace-payload redaction hooks.
"""

from __future__ import annotations

import logging
import sys
from typing import Any
from uuid import UUID

import structlog

from app.core.config import LogConfig

_SHARED_PROCESSORS: list[Any] = [
    structlog.contextvars.merge_contextvars,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
    structlog.processors.StackInfoRenderer(),
    structlog.processors.format_exc_info,
]


def configure_logging(cfg: LogConfig) -> None:
    """Configure structlog and bridge the stdlib logging tree through it.

    Safe to call more than once (idempotent reconfiguration); call before the
    FastAPI app or a Celery worker emits its first log line.
    """
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if cfg.json_logs
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    structlog.configure(
        processors=[
            *_SHARED_PROCESSORS,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=_SHARED_PROCESSORS,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(cfg.level.upper())

    # Route server/worker loggers through the root handler (no double output).
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "celery", "sqlalchemy.engine"):
        noisy = logging.getLogger(name)
        noisy.handlers.clear()
        noisy.propagate = True


def bind_request_context(
    request_id: str,
    org_id: UUID | None = None,
    user_id: UUID | None = None,
) -> None:
    """Bind per-request correlation fields into structlog contextvars."""
    structlog.contextvars.bind_contextvars(request_id=request_id)
    if org_id is not None:
        structlog.contextvars.bind_contextvars(org_id=str(org_id))
    if user_id is not None:
        structlog.contextvars.bind_contextvars(user_id=str(user_id))


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structlog bound logger."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
