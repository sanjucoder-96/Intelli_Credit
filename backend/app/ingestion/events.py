"""Ingestion progress events: Redis pub/sub for live SSE (ADR-0014).

Redis pub/sub is live fan-out ONLY — reconnect gap replay rehydrates from the
``document_stages`` table (the API's SSE endpoint queries it before
subscribing), exactly the mechanism ADR-0012/0014 fixed.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.logging import get_logger

logger = get_logger("app.ingestion.events")


def case_channel(case_id: UUID | str) -> str:
    return f"case:{case_id}:events"


def stage_event(
    *,
    document_id: UUID,
    stage: str,
    status: str,
    attempt: int,
    error: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The wire shape of one progress event (also the SSE ``data`` payload)."""
    return {
        "type": "document_stage",
        "document_id": str(document_id),
        "stage": stage,
        "status": status,
        "attempt": attempt,
        "error": error,
        "ts": datetime.now(UTC).isoformat(),
    }


async def publish_event(redis: Any, case_id: UUID | str | None, event: dict[str, Any]) -> None:
    """Publish one event to the case channel (no-op without a case)."""
    if case_id is None:
        return
    try:
        await redis.publish(case_channel(case_id), json.dumps(event))
    except Exception as exc:  # progress events must never fail the pipeline
        logger.warning("event_publish_failed", error=str(exc))
