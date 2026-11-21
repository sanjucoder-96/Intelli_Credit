"""Celery task shells (ADR-0004).

Tasks stay thin: deserialize arguments and call an application use case.
Phase 4 adds the ten-stage ingestion chain; Phase 5 adds workflow-run tasks.
"""

from app.tasks.celery_app import celery_app

__all__ = ["celery_app"]
