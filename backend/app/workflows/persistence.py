"""Workflow definition + execution persistence (S3.3).

Two clearly distinct records (ARCHITECTURE §14):

- ``workflow_executions`` — the lifecycle/state of one whole run (PENDING →
  RUNNING → PAUSED_HITL → COMPLETED/FAILED), pinned to the immutable
  ``workflows`` definition row it executed and to a ``thread_id`` (= run id)
  that also keys the LangGraph checkpoints.
- ``agent_executions`` — the individual agent/step traces, written by
  :class:`~app.agents.trace.DbTraceWriter` tagged with the same
  ``workflow_execution_id``. The two stay correlated: one execution row, many
  trace rows, all joinable by ``workflow_execution_id``.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import WorkflowExecutionStatus
from app.infrastructure.db.models import Workflow, WorkflowExecution
from app.workflows.definition import WorkflowDefinition


def definition_checksum(definition: WorkflowDefinition) -> str:
    """Content hash of a definition (matches the in-process registry)."""
    return hashlib.sha256(definition.model_dump_json(exclude_none=True).encode()).hexdigest()


async def ensure_workflow_row(session: AsyncSession, definition: WorkflowDefinition) -> Workflow:
    """Upsert the immutable ``workflows`` row for (key, version).

    Re-registering identical content is a no-op; different content for an
    existing version is rejected — versions are immutable (ADR-0008).
    """
    checksum = definition_checksum(definition)
    existing = (
        await session.execute(
            sa.select(Workflow).where(
                Workflow.key == definition.key, Workflow.version == definition.version
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.checksum != checksum:
            raise ValueError(
                f"Workflow '{definition.key}' v{definition.version} is immutable; "
                "content changed — register a new version"
            )
        return existing
    row = Workflow(
        key=definition.key,
        version=definition.version,
        definition=definition.model_dump(mode="json", exclude_none=True),
        checksum=checksum,
        is_active=True,
    )
    session.add(row)
    await session.flush()
    return row


async def create_execution(
    session: AsyncSession,
    *,
    org_id: UUID,
    workflow_id: UUID,
    thread_id: str,
    loan_application_id: UUID | None = None,
    created_by: UUID | None = None,
) -> WorkflowExecution:
    """Insert a RUNNING execution row (idempotent on thread_id)."""
    existing = (
        await session.execute(
            sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == thread_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    execution = WorkflowExecution(
        org_id=org_id,
        workflow_id=workflow_id,
        loan_application_id=loan_application_id,
        thread_id=thread_id,
        status=WorkflowExecutionStatus.RUNNING,
        started_at=datetime.now(UTC),
        created_by=created_by,
    )
    session.add(execution)
    await session.flush()
    return execution


async def update_execution(
    session: AsyncSession,
    execution_id: UUID,
    *,
    status: WorkflowExecutionStatus,
    cost_usd: float | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    """Advance an execution's lifecycle state."""
    values: dict[str, Any] = {"status": status}
    if status in (
        WorkflowExecutionStatus.COMPLETED,
        WorkflowExecutionStatus.FAILED,
        WorkflowExecutionStatus.CANCELLED,
    ):
        values["finished_at"] = datetime.now(UTC)
    if cost_usd is not None:
        values["cost_usd"] = Decimal(str(round(cost_usd, 4)))
    if error is not None:
        values["error"] = error
    await session.execute(
        sa.update(WorkflowExecution).where(WorkflowExecution.id == execution_id).values(**values)
    )
