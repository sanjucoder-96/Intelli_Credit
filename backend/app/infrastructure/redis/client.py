"""Lazy singleton async Redis client and readiness probe.

The client is created on first use from :func:`app.core.config.get_settings`
so importing this module never requires configuration. Celery uses its own
broker connection (Phase 4 — ADR-0004); this client serves the API process:
readiness checks now, rate limiting and the LLM cache later (ADR-0005).
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.core.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    """Return the lazily created process-wide async Redis client.

    ONLY safe where one long-lived event loop owns the process (the API).
    Celery task bodies run ``asyncio.run()`` per task — a fresh loop every
    time — and an asyncio Redis client's internal locks bind to the loop
    that created them; use :func:`create_redis` there instead.
    """
    global _client
    if _client is None:
        _client = Redis.from_url(get_settings().redis.url, decode_responses=True)
    return _client


def create_redis() -> Redis:
    """A fresh client for short-lived event loops (Celery pipeline stages)."""
    client: Redis = Redis.from_url(get_settings().redis.url, decode_responses=True)
    return client


async def ping_redis() -> bool:
    """Readiness probe: PING against the configured Redis."""
    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


async def close_redis() -> None:
    """Close the singleton client (application shutdown)."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
