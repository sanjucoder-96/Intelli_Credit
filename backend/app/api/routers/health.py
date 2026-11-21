"""Liveness and readiness endpoints.

``/health`` is static (no I/O) so orchestrators can distinguish a hung
process from unavailable dependencies, which ``/ready`` probes explicitly.
Phase 6 extends readiness with an ML-artifact check (champion models loaded).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.providers import get_settings_dep
from app.api.schemas.common import HealthResponse, ReadyResponse
from app.core.config import Settings
from app.core.errors import ServiceUnavailableError
from app.infrastructure.db.engine import ping_database
from app.infrastructure.redis.client import ping_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> HealthResponse:
    """Liveness probe — answers from process state only, never touches I/O."""
    return HealthResponse(
        status="ok",
        version=settings.app.version,
        environment=settings.app.env,
    )


@router.get("/ready")
async def ready() -> ReadyResponse:
    """Readiness probe — pings PostgreSQL and Redis.

    Returns 200 with per-dependency booleans when everything is reachable;
    raises :class:`ServiceUnavailableError` (503 envelope, checks in
    ``details``) when any dependency fails.
    """
    checks = {
        "database": await ping_database(),
        "redis": await ping_redis(),
    }
    if not all(checks.values()):
        raise ServiceUnavailableError(
            "One or more dependencies are unavailable",
            details={"checks": checks},
        )
    return ReadyResponse(status="ok", checks=checks)
