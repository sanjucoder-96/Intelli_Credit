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

import time
from typing import Any

from celery import Celery
from celery.signals import before_task_publish, setup_logging, task_postrun, task_prerun
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.metrics import observe_celery_task


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


# --------------------------------------------------------------------------- #
# Task observability (Phase 9): queue latency (enqueue→start), task duration,
# and success/failure — for every task, in one place. Best-effort: a metrics
# hiccup never affects task execution.
# --------------------------------------------------------------------------- #
_TASK_STARTS: dict[str, float] = {}


@before_task_publish.connect
def _stamp_enqueue_time(headers: dict[str, Any] | None = None, **_kwargs: Any) -> None:
    """Stamp the publish time into the message headers so the worker can
    measure how long the task waited in the queue."""
    if headers is not None:
        headers["ic_enqueued_at"] = time.time()


@task_prerun.connect
def _on_task_prerun(task_id: str | None = None, task: Any = None, **_kwargs: Any) -> None:
    if task_id is not None:
        _TASK_STARTS[task_id] = time.perf_counter()
    enqueued_at = getattr(getattr(task, "request", None), "ic_enqueued_at", None)
    if enqueued_at:
        name = getattr(task, "name", "unknown")
        observe_celery_task(name, "started", queue_latency_s=time.time() - float(enqueued_at))


@task_postrun.connect
def _on_task_postrun(
    task_id: str | None = None, task: Any = None, state: str | None = None, **_kwargs: Any
) -> None:
    started = _TASK_STARTS.pop(task_id, None) if task_id is not None else None
    name = getattr(task, "name", "unknown")
    status = "success" if state == "SUCCESS" else "failure"
    duration_s = (time.perf_counter() - started) if started is not None else 0.0
    observe_celery_task(name, status, duration_s=duration_s)
