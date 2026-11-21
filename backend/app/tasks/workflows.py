"""Celery ownership of durable workflow executions (S3.5).

A workflow run is owned by a Celery task, not an HTTP request: the API enqueues
``workflow.run`` and returns immediately, and the worker drives the graph over
the Postgres checkpointer until it either pauses at a HITL gate or completes.
HITL continuation is a second task, ``workflow.resume``, enqueued when a human
submits their input.

Safe restart is a property of the checkpoint, not of delivery. Both tasks run
with ``acks_late`` (global Celery config) and deterministic task ids, so a
worker lost mid-run redelivers the same task; :class:`WorkflowRunner` then
re-enters from the last checkpoint — completed nodes are never re-executed and
their DB/trace writes are never duplicated (proven by
``tests/test_workflow_durable.py`` and the ``start`` idempotency guard in the
runner).
"""

from __future__ import annotations

import asyncio
import selectors
import sys
from collections.abc import Awaitable
from typing import Any, TypeVar
from uuid import UUID

import sqlalchemy as sa

from app.core.config import get_settings
from app.core.context import TenantContext
from app.core.logging import get_logger
from app.domain.enums import Role, WorkflowExecutionStatus
from app.infrastructure.db.engine import get_session_factory
from app.infrastructure.db.models import Workflow, WorkflowExecution
from app.infrastructure.redis.client import create_redis
from app.tasks.celery_app import celery_app
from app.workflows.bootstrap import load_workflows
from app.workflows.checkpointer import build_checkpointer
from app.workflows.definition import WorkflowDefinition
from app.workflows.registry import workflow_registry
from app.workflows.runner import RunOutcome, WorkflowRunner
from app.workflows.runtime import build_runtime_bundle, make_context_factory
from app.workflows.state import initial_state

logger = get_logger("app.tasks.workflows")

_QUEUE = "agents"
_SERVICE_USER = UUID("00000000-0000-0000-0000-000000000000")
_TERMINAL = frozenset(
    {
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.CANCELLED,
    }
)

T = TypeVar("T")


def _run_async(coro: Awaitable[T]) -> T:
    """Run a coroutine on a SelectorEventLoop.

    The durable checkpointer uses psycopg, which requires a selector loop on
    Windows (the Proactor default is unsupported for async psycopg). On Linux
    the default loop is already selector-based; forcing it here is harmless and
    keeps local Windows workers functional.
    """
    if sys.platform == "win32":
        loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
    else:
        loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _service_context(org_id: str, created_by: str | None) -> TenantContext:
    """A tenant-scoped principal for a background run (org_id is what tags rows)."""
    return TenantContext(
        org_id=UUID(org_id),
        user_id=UUID(created_by) if created_by else _SERVICE_USER,
        role=Role.CREDIT_OFFICER,
    )


# --------------------------------------------------------------- enqueue helpers
def enqueue_workflow_start(
    *,
    run_id: str,
    workflow_key: str,
    org_id: str,
    case_id: str | None = None,
    version: int | None = None,
    scratch: dict[str, Any] | None = None,
    created_by: str | None = None,
) -> None:
    """Enqueue the owning ``workflow.run`` task (deterministic id = run id)."""
    celery_app.send_task(
        "workflow.run",
        kwargs={
            "run_id": run_id,
            "workflow_key": workflow_key,
            "version": version,
            "org_id": org_id,
            "case_id": case_id,
            "scratch": scratch or {},
            "created_by": created_by,
        },
        queue=_QUEUE,
        task_id=f"wf-run:{run_id}",
    )


def enqueue_workflow_resume(*, run_id: str, resume_state: dict[str, Any] | None = None) -> None:
    """Enqueue a ``workflow.resume`` after a human submits HITL input."""
    celery_app.send_task(
        "workflow.resume",
        kwargs={"run_id": run_id, "resume_state": resume_state or {}},
        queue=_QUEUE,
        task_id=f"wf-resume:{run_id}",
    )


# ------------------------------------------------------------------- async cores
async def _run(
    *,
    run_id: str,
    workflow_key: str,
    version: int | None,
    org_id: str,
    case_id: str | None,
    scratch: dict[str, Any],
    created_by: str | None,
) -> RunOutcome:
    load_workflows()
    definition = workflow_registry.get(workflow_key, version)
    session_factory = get_session_factory()
    tenant = _service_context(org_id, created_by)
    redis = create_redis()

    def builder(execution_id: UUID) -> Any:
        bundle = build_runtime_bundle(
            session_factory=session_factory,
            org_id=UUID(org_id),
            execution_id=execution_id,
            run_id=run_id,
            tenant=tenant,
            settings=get_settings(),
            redis=redis,
            case_id=UUID(case_id) if case_id else None,
        )
        return make_context_factory(bundle, definition)

    state = initial_state(run_id, workflow_key)
    if scratch:
        state["scratch"] = scratch
    runner = WorkflowRunner(session_factory)
    try:
        async with build_checkpointer() as checkpointer:
            return await runner.start(
                definition,
                state,
                org_id=UUID(org_id),
                checkpointer=checkpointer,
                context_factory_builder=builder,
                loan_application_id=UUID(case_id) if case_id else None,
                created_by=UUID(created_by) if created_by else None,
            )
    finally:
        await redis.aclose()


async def _resume(*, run_id: str, resume_state: dict[str, Any]) -> RunOutcome | None:
    load_workflows()
    session_factory = get_session_factory()
    async with session_factory() as session:
        execution = (
            await session.execute(
                sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
            )
        ).scalar_one_or_none()
        if execution is None:
            logger.warning("workflow_resume_unknown_thread", run_id=run_id)
            return None
        if execution.status in _TERMINAL:
            logger.info("workflow_resume_already_terminal", run_id=run_id, status=execution.status)
            return None
        workflow_row = await session.get(Workflow, execution.workflow_id)
        org_id = str(execution.org_id)
        case_id = str(execution.loan_application_id) if execution.loan_application_id else None
        created_by = str(execution.created_by) if execution.created_by else None
    assert workflow_row is not None
    definition = WorkflowDefinition.model_validate(workflow_row.definition)

    tenant = _service_context(org_id, created_by)
    redis = create_redis()

    def builder(execution_id: UUID) -> Any:
        bundle = build_runtime_bundle(
            session_factory=session_factory,
            org_id=UUID(org_id),
            execution_id=execution_id,
            run_id=run_id,
            tenant=tenant,
            settings=get_settings(),
            redis=redis,
            case_id=UUID(case_id) if case_id else None,
        )
        return make_context_factory(bundle, definition)

    runner = WorkflowRunner(session_factory)
    try:
        async with build_checkpointer() as checkpointer:
            return await runner.resume(
                definition,
                thread_id=run_id,
                checkpointer=checkpointer,
                resume_state=resume_state or None,
                context_factory_builder=builder,
            )
    finally:
        await redis.aclose()


# ------------------------------------------------------------------------- tasks
@celery_app.task(name="workflow.run", bind=True, acks_late=True)
def run_workflow_task(self: Any, **kwargs: Any) -> dict[str, Any]:
    """Own a workflow run to its first pause or completion (idempotent)."""
    outcome = _run_async(_run(**kwargs))
    result = {
        "run_id": outcome.run_id,
        "execution_id": str(outcome.execution_id),
        "status": outcome.status.value,
        "interrupted": outcome.interrupted,
    }
    logger.info("workflow_task_settled", **result)
    return result


@celery_app.task(name="workflow.resume", bind=True, acks_late=True)
def resume_workflow_task(self: Any, **kwargs: Any) -> dict[str, Any]:
    """Continue a paused run after HITL input (idempotent; noop if terminal)."""
    outcome = _run_async(_resume(**kwargs))
    if outcome is None:
        return {"run_id": kwargs.get("run_id"), "status": "noop"}
    result = {
        "run_id": outcome.run_id,
        "execution_id": str(outcome.execution_id),
        "status": outcome.status.value,
        "interrupted": outcome.interrupted,
    }
    logger.info("workflow_resume_settled", **result)
    return result
