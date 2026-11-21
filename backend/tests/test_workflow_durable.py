"""Durable workflow execution (S3.2–3.5): Postgres checkpoints, lifecycle
rows, worker-death + resume without re-executing completed nodes or
duplicating persistence. Real Postgres; noop agents stand in for the graph.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa

from app.domain.enums import DataOrigin, WorkflowExecutionStatus
from app.infrastructure.db.models import Company, LoanApplication, Organization, WorkflowExecution
from app.workflows.checkpointer import build_checkpointer
from app.workflows.definition import NodeSpec, WorkflowDefinition
from app.workflows.runner import WorkflowRunner
from app.workflows.state import initial_state
from tests.support import admin_engine, session_factory

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("IC_INTEGRATION_TESTS"),
        reason="live Postgres required — set IC_INTEGRATION_TESTS=1",
    ),
]


def _hitl_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="durable_hitl",
        version=1,
        nodes=[
            NodeSpec(name="analyze", agent="noop"),
            NodeSpec(name="review", agent="noop", after=["analyze"], interrupt=True),
            NodeSpec(name="decide", agent="noop", after=["review"]),
        ],
    )


async def _seed_case(engine: object) -> tuple[uuid.UUID, uuid.UUID]:
    factory = session_factory(engine)  # type: ignore[arg-type]
    async with factory() as session:
        suffix = uuid.uuid4().hex[:8]
        org = Organization(name=f"Durable {suffix}", slug=f"dur-{suffix}", is_active=True)
        session.add(org)
        await session.flush()
        company = Company(
            org_id=org.id, legal_name=f"Acme {suffix}", data_origin=DataOrigin.SYNTHETIC_DEMO
        )
        session.add(company)
        await session.flush()
        case = LoanApplication(org_id=org.id, company_id=company.id, title=f"Durable case {suffix}")
        session.add(case)
        await session.commit()
        return org.id, case.id


def _nodes(state: dict) -> list[str]:
    return [finding["node"] for finding in state.get("findings", [])]


async def test_checkpoint_pause_kill_resume_without_duplication() -> None:
    async with admin_engine() as engine:
        org_id, case_id = await _seed_case(engine)
        runner = WorkflowRunner(session_factory(engine))
        definition = _hitl_definition()
        run_id = str(uuid.uuid4())
        state = initial_state(run_id, "durable_hitl")

        # --- start: runs 'analyze', pauses before the HITL gate ---
        async with build_checkpointer() as saver:
            started = await runner.start(
                definition, state, org_id=org_id, loan_application_id=case_id, checkpointer=saver
            )
        assert started.interrupted is True
        assert started.status is WorkflowExecutionStatus.PAUSED_HITL
        assert _nodes(started.state) == ["analyze"]  # only the pre-interrupt node ran

        # execution row persisted, PAUSED_HITL, correlated by thread_id
        factory = session_factory(engine)
        async with factory() as session:
            execution = (
                await session.execute(
                    sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
                )
            ).scalar_one()
            assert execution.status is WorkflowExecutionStatus.PAUSED_HITL
            assert execution.loan_application_id == case_id
            assert execution.started_at is not None

        # --- "worker dies": the saver above is closed. Resume on a fresh one ---
        async with build_checkpointer() as fresh_saver:
            resumed = await runner.resume(definition, thread_id=run_id, checkpointer=fresh_saver)
        assert resumed.status is WorkflowExecutionStatus.COMPLETED
        assert resumed.interrupted is False
        assert set(_nodes(resumed.state)) == {"analyze", "review", "decide"}
        # completed work not re-executed: 'analyze' ran exactly once across the two invocations
        assert _nodes(resumed.state).count("analyze") == 1

        # exactly ONE execution row (no duplicate on resume), now COMPLETED
        async with factory() as session:
            rows = list(
                (
                    await session.execute(
                        sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
                    )
                ).scalars()
            )
            assert len(rows) == 1
            assert rows[0].status is WorkflowExecutionStatus.COMPLETED
            assert rows[0].finished_at is not None


async def test_execution_row_correlates_with_workflow_definition() -> None:
    async with admin_engine() as engine:
        org_id, _case_id = await _seed_case(engine)
        runner = WorkflowRunner(session_factory(engine))
        definition = WorkflowDefinition(
            key="durable_linear",
            version=1,
            nodes=[NodeSpec(name="only", agent="noop")],
        )
        run_id = str(uuid.uuid4())
        async with build_checkpointer() as saver:
            outcome = await runner.start(
                definition,
                initial_state(run_id, "durable_linear"),
                org_id=org_id,
                checkpointer=saver,
            )
        assert outcome.status is WorkflowExecutionStatus.COMPLETED
        factory = session_factory(engine)
        async with factory() as session:
            execution = (
                await session.execute(
                    sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
                )
            ).scalar_one()
            # the execution points at an immutable workflows row for this (key, version)
            from app.infrastructure.db.models import Workflow

            workflow = await session.get(Workflow, execution.workflow_id)
            assert workflow is not None
            assert workflow.key == "durable_linear" and workflow.version == 1
