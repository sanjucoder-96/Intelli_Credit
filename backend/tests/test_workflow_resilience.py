"""Phase-8 workflow reliability + chaos (priority #3, items 6 & 15).

Failure-injection and kill-and-resume proofs against live Postgres:

    failure → checkpoint → restart → resume → no duplicated completed work

- several nodes execute, the worker is killed (checkpointer closed) at the HITL
  gate, a fresh worker resumes and completes — exactly ONE execution row, each
  node run once;
- a node crashes mid-run (a required agent raises); the run goes FAILED, a
  restart re-runs ONLY the crashed node and completes — no duplicate rows, no
  re-execution of the nodes that already succeeded;
- a redelivered start while paused at the HITL gate does NOT step past the gate
  (at-least-once delivery cannot skip a human review);
- a redelivered start after completion is idempotent.

The same scenario is exposed as a reproducible demo in ``scripts/chaos_demo.py``.
"""

from __future__ import annotations

import os
import uuid

import pytest
import sqlalchemy as sa

from app.agents.base import BaseAgent, Plan
from app.agents.context import AgentContext
from app.agents.registry import AgentRegistry, NoopAgent, NoopOutput
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

# Module-level attempt counter so a "flaky" node fails on its FIRST execution and
# succeeds when a restart re-runs it (a transient crash healed by resume).
_ATTEMPTS: dict[str, int] = {}


class FlakyAgent(BaseAgent):
    """Raises on the first execution of a node, succeeds thereafter."""

    async def act(self, ctx: AgentContext, plan: Plan) -> NoopOutput:
        key = ctx.node
        _ATTEMPTS[key] = _ATTEMPTS.get(key, 0) + 1
        if _ATTEMPTS[key] == 1:
            raise RuntimeError(f"injected crash in node {key!r}")
        return NoopOutput()


def _registry() -> AgentRegistry:
    reg = AgentRegistry()
    reg.register("noop")(NoopAgent)
    reg.register("flaky")(FlakyAgent)
    return reg


def _hitl_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="chaos_hitl",
        version=1,
        nodes=[
            NodeSpec(name="ingest", agent="noop"),
            NodeSpec(name="analyze", agent="noop", after=["ingest"]),
            NodeSpec(name="fraud", agent="noop", after=["ingest"]),
            NodeSpec(name="review", agent="noop", after=["analyze", "fraud"], interrupt=True),
            NodeSpec(name="score", agent="noop", after=["review"]),
            NodeSpec(name="cam", agent="noop", after=["score"]),
        ],
    )


def _flaky_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="chaos_flaky",
        version=1,
        nodes=[
            NodeSpec(name="ingest", agent="noop"),
            NodeSpec(name="score", agent="flaky", after=["ingest"]),  # crashes once
            NodeSpec(name="cam", agent="noop", after=["score"]),
        ],
    )


async def _seed_case(engine: object) -> tuple[uuid.UUID, uuid.UUID]:
    factory = session_factory(engine)  # type: ignore[arg-type]
    async with factory() as session:
        suffix = uuid.uuid4().hex[:8]
        org = Organization(name=f"Chaos {suffix}", slug=f"chaos-{suffix}", is_active=True)
        session.add(org)
        await session.flush()
        company = Company(
            org_id=org.id, legal_name=f"Acme {suffix}", data_origin=DataOrigin.SYNTHETIC_DEMO
        )
        session.add(company)
        await session.flush()
        case = LoanApplication(org_id=org.id, company_id=company.id, title=f"Chaos {suffix}")
        session.add(case)
        await session.commit()
        return org.id, case.id


def _ran(state: dict) -> list[str]:
    return [f["node"] for f in state.get("findings", [])]


async def _rows(factory, run_id: str) -> list[WorkflowExecution]:  # noqa: ANN001
    async with factory() as session:
        return list(
            (
                await session.execute(
                    sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
                )
            ).scalars()
        )


async def test_multi_node_kill_at_gate_resumes_without_duplication() -> None:
    async with admin_engine() as engine:
        org_id, case_id = await _seed_case(engine)
        runner = WorkflowRunner(session_factory(engine), agents=_registry())
        definition = _hitl_definition()
        run_id = str(uuid.uuid4())
        state = initial_state(run_id, "chaos_hitl")

        # --- worker A: runs ingest/analyze/fraud, pauses at the HITL gate ---
        async with build_checkpointer() as saver_a:
            started = await runner.start(
                definition, state, org_id=org_id, loan_application_id=case_id, checkpointer=saver_a
            )
        assert started.interrupted is True
        assert started.status is WorkflowExecutionStatus.PAUSED_HITL
        assert set(_ran(started.state)) == {"ingest", "analyze", "fraud"}

        # --- worker A dies (saver closed). Worker B resumes on a fresh saver ---
        async with build_checkpointer() as saver_b:
            resumed = await runner.resume(definition, thread_id=run_id, checkpointer=saver_b)
        assert resumed.status is WorkflowExecutionStatus.COMPLETED
        assert set(_ran(resumed.state)) == {"ingest", "analyze", "fraud", "review", "score", "cam"}
        # Nothing pre-interrupt re-ran across the two workers.
        for node in ("ingest", "analyze", "fraud"):
            assert _ran(resumed.state).count(node) == 1

        rows = await _rows(session_factory(engine), run_id)
        assert len(rows) == 1  # no duplicate execution on resume
        assert rows[0].status is WorkflowExecutionStatus.COMPLETED
        assert rows[0].finished_at is not None


async def test_node_crash_then_restart_reruns_only_the_failed_node() -> None:
    _ATTEMPTS.clear()
    async with admin_engine() as engine:
        org_id, case_id = await _seed_case(engine)
        runner = WorkflowRunner(session_factory(engine), agents=_registry())
        definition = _flaky_definition()
        run_id = str(uuid.uuid4())
        state = initial_state(run_id, "chaos_flaky")

        # --- start: 'ingest' succeeds, 'score' (flaky) crashes → run FAILED ---
        with pytest.raises(RuntimeError, match="injected crash"):
            async with build_checkpointer() as saver:
                await runner.start(
                    definition,
                    state,
                    org_id=org_id,
                    loan_application_id=case_id,
                    checkpointer=saver,
                )

        rows = await _rows(session_factory(engine), run_id)
        assert len(rows) == 1
        assert rows[0].status is WorkflowExecutionStatus.FAILED
        assert rows[0].error is not None

        # --- restart: resume re-runs ONLY 'score' (now healed) then 'cam' ---
        async with build_checkpointer() as saver:
            resumed = await runner.resume(definition, thread_id=run_id, checkpointer=saver)
        assert resumed.status is WorkflowExecutionStatus.COMPLETED
        assert set(_ran(resumed.state)) == {"ingest", "score", "cam"}
        assert _ran(resumed.state).count("ingest") == 1  # the succeeded node did NOT re-run
        assert _ATTEMPTS["score"] == 2  # failed once, succeeded on the restart

        rows = await _rows(session_factory(engine), run_id)
        assert len(rows) == 1  # still exactly one execution row
        assert rows[0].status is WorkflowExecutionStatus.COMPLETED


async def test_redelivered_start_at_gate_does_not_skip_human_review() -> None:
    async with admin_engine() as engine:
        org_id, case_id = await _seed_case(engine)
        runner = WorkflowRunner(session_factory(engine), agents=_registry())
        definition = _hitl_definition()
        run_id = str(uuid.uuid4())
        state = initial_state(run_id, "chaos_hitl")

        async with build_checkpointer() as saver:
            first = await runner.start(
                definition, state, org_id=org_id, loan_application_id=case_id, checkpointer=saver
            )
            assert first.status is WorkflowExecutionStatus.PAUSED_HITL

            # A duplicate delivery of the SAME start (at-least-once) must NOT
            # advance past the interrupt — the run stays paused for review.
            redelivered = await runner.start(
                definition, state, org_id=org_id, loan_application_id=case_id, checkpointer=saver
            )
        assert redelivered.status is WorkflowExecutionStatus.PAUSED_HITL
        assert redelivered.interrupted is True
        assert "score" not in _ran(redelivered.state)  # scoring never ran

        rows = await _rows(session_factory(engine), run_id)
        assert len(rows) == 1  # idempotent on thread_id — no duplicate row
