"""The document-ingestion pipeline (ARCHITECTURE §7).

Ten small idempotent stages per document, each checkpointed in
``document_stages`` (ARCHITECTURE §15.3): Celery delivers at-least-once, the
checkpoint makes effects exactly-once. Stage handlers live here; the thin
Celery shells live in ``app.tasks.ingest``; deterministic parsing lives in
``app.docintel``. Progress streams to the UI over Redis pub/sub → SSE.
"""

from app.ingestion.stages import STAGE_ORDER, run_stage

__all__ = ["STAGE_ORDER", "run_stage"]
