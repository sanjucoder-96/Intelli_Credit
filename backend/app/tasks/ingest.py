"""Celery shells for the ingestion pipeline (ADR-0004).

One generic ``ingest.run_stage`` task drives the whole chain: deterministic
task IDs (``{document_id}:{stage}``), ``acks_late`` redelivery made safe by
the ``document_stages`` checkpoint (ARCHITECTURE §15.3), per-stage queue
routing (OCR rides the low-concurrency ``ocr`` queue), exponential-backoff
retries, and chain advancement + ZIP/EML fan-out on completion.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from app.core.logging import get_logger
from app.domain.enums import IngestStage
from app.ingestion.stages import next_stage, run_stage
from app.tasks.celery_app import celery_app

logger = get_logger("app.tasks.ingest")

_STAGE_QUEUES: dict[IngestStage, str] = {IngestStage.OCR_PAGES: "ocr"}
_DEFAULT_QUEUE = "ingest"


def enqueue_stage(document_id: UUID | str, stage: IngestStage) -> None:
    """Send one (document, stage) execution with a deterministic task id."""
    celery_app.send_task(
        "ingest.run_stage",
        args=[str(document_id), stage.value],
        queue=_STAGE_QUEUES.get(stage, _DEFAULT_QUEUE),
        task_id=f"{document_id}:{stage.value}",
    )


def start_document_pipeline(document_id: UUID | str) -> None:
    """Kick off the chain for a freshly registered document (API calls this)."""
    enqueue_stage(document_id, IngestStage.DETECT_TYPE)


@celery_app.task(
    name="ingest.run_stage",
    bind=True,
    acks_late=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=5,
)
def run_stage_task(self: Any, document_id: str, stage_value: str) -> dict[str, Any]:
    """Run one stage; on success advance the chain and fan out children."""
    stage = IngestStage(stage_value)
    result = asyncio.run(run_stage(UUID(document_id), stage))

    for child_id in result.extra_documents or []:
        logger.info("pipeline_fanout_child", parent=document_id, child=str(child_id))
        start_document_pipeline(child_id)

    if result.advance:
        following = next_stage(stage)
        if following is not None:
            enqueue_stage(document_id, following)

    return {
        "document_id": document_id,
        "stage": stage.value,
        "advanced": result.advance,
        "children": [str(child) for child in result.extra_documents or []],
    }
