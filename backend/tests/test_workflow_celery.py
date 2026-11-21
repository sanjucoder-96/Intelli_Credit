"""Celery ownership of durable runs (S3.5): the task cores drive a run over
the Postgres checkpointer, and an ``acks_late`` redelivery (calling the same
core twice) resumes from the checkpoint instead of re-executing completed work.

Uses noop agents so the ownership/idempotency wiring is tested offline, with no
LLM. The credit-appraisal path through the same runner is covered end to end in
``tests/test_credit_appraisal.py``.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import sqlalchemy as sa

from app.domain.enums import WorkflowExecutionStatus
from app.infrastructure.db.models import Organization, WorkflowExecution
from app.tasks.workflows import _resume, _run
from app.workflows.definition import NodeSpec, WorkflowDefinition
from app.workflows.registry import workflow_registry
from tests.support import admin_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]


def _register_noop_hitl() -> WorkflowDefinition:
    definition = WorkflowDefinition(
        key="celery_hitl_noop",
        version=1,
        nodes=[
            NodeSpec(name="analyze", agent="noop"),
            NodeSpec(name="review", agent="noop", after=["analyze"], interrupt=True),
            NodeSpec(name="decide", agent="noop", after=["review"]),
        ],
    )
    workflow_registry.register(definition)
    return definition


async def _seed_org(engine: Any) -> str:
    factory = session_factory(engine)
    async with factory() as session:
        suffix = uuid.uuid4().hex[:8]
        org = Organization(name=f"Celery {suffix}", slug=f"cel-{suffix}", is_active=True)
        session.add(org)
        await session.commit()
        return str(org.id)


def _nodes(state: dict[str, Any]) -> list[str]:
    return [f["node"] for f in state.get("findings", [])]


async def test_celery_task_owns_run_and_redelivery_is_idempotent(monkeypatch: Any) -> None:
    async with admin_engine() as engine:
        _register_noop_hitl()
        org_id = await _seed_org(engine)
        run_id = str(uuid.uuid4())

        # The task cores use the process session factory; point it at the test DB.
        factory = session_factory(engine)
        monkeypatch.setattr("app.tasks.workflows.get_session_factory", lambda: factory)

        run_kwargs = dict(
            run_id=run_id,
            workflow_key="celery_hitl_noop",
            version=1,
            org_id=org_id,
            case_id=None,
            scratch={},
            created_by=None,
        )

        # --- worker picks up the task: runs 'analyze', pauses at the HITL gate ---
        first = await _run(**run_kwargs)
        assert first.status is WorkflowExecutionStatus.PAUSED_HITL
        assert _nodes(first.state) == ["analyze"]

        # --- acks_late redelivery: SAME task runs again → resumes, no duplication ---
        second = await _run(**run_kwargs)
        assert second.status is WorkflowExecutionStatus.PAUSED_HITL
        assert _nodes(second.state).count("analyze") == 1  # not re-executed

        # exactly one execution row despite two deliveries
        async with factory() as session:
            rows = list(
                (
                    await session.execute(
                        sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
                    )
                ).scalars()
            )
        assert len(rows) == 1
        assert rows[0].status is WorkflowExecutionStatus.PAUSED_HITL

        # --- human submits input → resume task completes the run ---
        resumed = await _resume(run_id=run_id, resume_state={})
        assert resumed is not None
        assert resumed.status is WorkflowExecutionStatus.COMPLETED
        assert set(_nodes(resumed.state)) == {"analyze", "review", "decide"}
        assert _nodes(resumed.state).count("analyze") == 1

        # --- resume redelivery after completion is a safe noop ---
        again = await _resume(run_id=run_id, resume_state={})
        assert again is None

        async with factory() as session:
            final = (
                await session.execute(
                    sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
                )
            ).scalar_one()
        assert final.status is WorkflowExecutionStatus.COMPLETED
        assert final.finished_at is not None
