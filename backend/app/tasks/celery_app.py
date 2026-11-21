"""Celery application factory and configuration (ADR-0004).

Redis is broker and result backend; queues follow the canonical topology
``agents`` / ``ingest`` / ``ocr`` (ML jobs ride the low-concurrency ``ocr``
queue). Reliability defaults: ``task_acks_late`` + ``task_reject_on_worker_lost``
so a killed worker redelivers, paired with idempotent task bodies
(ARCHITECTURE §15.3). Workers run with a threads/solo pool for a clean
per-task asyncio story (prefork works in the Linux container but is avoided
for behavioural parity with native-Windows development runs).

This module must never import the FastAPI app — the worker image boots
without the HTTP stack.
"""

from __future__ import annotations

from typing import Any

from celery import Celery
from celery.signals import setup_logging
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import configure_logging


def _build_celery() -> Celery:
    """Construct the Celery app from validated settings (fail-fast)."""
    settings = get_settings()
    app = Celery("intellicredit")
    app.conf.update(
        broker_url=settings.celery.broker_url,
        result_backend=settings.celery.result_backend,
        task_default_queue=settings.celery.default_queue,
        # Queues declared explicitly from config (the authoritative list); a
        # typo'd -Q flag on a worker then fails loudly instead of silently
        # consuming from a fresh empty queue.
        task_queues=[Queue(name) for name in settings.celery.queues],
        task_routes={
            "platform.*": {"queue": "agents"},
            "workflow.*": {"queue": "agents"},
            "ingest.*": {"queue": "ingest"},
        },
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        worker_prefetch_multiplier=1,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Workers subscribe explicitly: celery worker -Q agents,ingest,ocr
        beat_schedule={
            "platform-heartbeat": {
                "task": "platform.heartbeat",
                "schedule": 300.0,
                "options": {"queue": "agents"},
            },
        },
        imports=("app.tasks.sample", "app.tasks.ingest", "app.tasks.workflows"),
    )
    return app


celery_app = _build_celery()


@setup_logging.connect
def _configure_celery_logging(**_kwargs: Any) -> None:
    """Route worker/beat logs through structlog instead of Celery's defaults."""
    configure_logging(get_settings().log)
