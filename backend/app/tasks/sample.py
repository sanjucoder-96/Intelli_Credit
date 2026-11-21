"""Sample background task proving worker/beat/broker wiring end-to-end.

``platform.heartbeat`` is scheduled by beat every five minutes and can be
invoked ad hoc (``celery_app.send_task("platform.heartbeat")``). Real
pipelines (ingestion stages, workflow runs) land in Phases 4-5 as separate
modules following this shape: thin shell, structured logging, JSON result.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.logging import get_logger
from app.tasks.celery_app import celery_app

logger = get_logger("app.tasks.sample")


@celery_app.task(name="platform.heartbeat", bind=True)
def heartbeat(self: Any) -> dict[str, str]:
    """Log a structured heartbeat and return a JSON-serializable payload."""
    now = datetime.now(UTC).isoformat()
    logger.info(
        "platform_heartbeat",
        task_id=self.request.id,
        queue=self.request.delivery_info.get("routing_key") if self.request.delivery_info else None,
        ts=now,
    )
    return {"status": "ok", "ts": now}
