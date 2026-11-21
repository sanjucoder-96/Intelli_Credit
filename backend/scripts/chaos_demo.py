"""Chaos kill-and-resume demo (Phase-8 item 15).

A reproducible, narrated demonstration of IntelliCredit's durable execution: a
workflow runs several nodes, the worker is KILLED at the human-review gate, a
fresh worker resumes from the Postgres checkpoint, completes the run, and we
verify there is exactly ONE execution row and no node ran twice.

    IC_INTEGRATION_TESTS=1 ./.venv/Scripts/python.exe -m scripts.chaos_demo

Uses IC_DATABASE__URL (defaults to the local test database).
"""

from __future__ import annotations

import asyncio
import os
import selectors
import sys
import uuid

import sqlalchemy as sa

os.environ.setdefault(
    "IC_DATABASE__URL",
    "postgresql+asyncpg://intellicredit:intellicredit@127.0.0.1:5432/intellicredit_test",
)
os.environ.setdefault("IC_AUTH__JWT_SECRET", "unit-test-secret-at-least-16-chars")

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from app.agents.registry import AgentRegistry, NoopAgent  # noqa: E402
from app.domain.enums import DataOrigin, WorkflowExecutionStatus  # noqa: E402
from app.infrastructure.db.models import (  # noqa: E402
    Company,
    LoanApplication,
    Organization,
    WorkflowExecution,
)
from app.workflows.checkpointer import build_checkpointer  # noqa: E402
from app.workflows.definition import NodeSpec, WorkflowDefinition  # noqa: E402
from app.workflows.runner import WorkflowRunner  # noqa: E402
from app.workflows.state import initial_state  # noqa: E402


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        key="chaos_demo",
        version=1,
        nodes=[
            NodeSpec(name="ingest", agent="noop"),
            NodeSpec(name="financial_analysis", agent="noop", after=["ingest"]),
            NodeSpec(name="fraud_detection", agent="noop", after=["ingest"]),
            NodeSpec(
                name="human_review",
                agent="noop",
                after=["financial_analysis", "fraud_detection"],
                interrupt=True,
            ),
            NodeSpec(name="risk_assessment", agent="noop", after=["human_review"]),
            NodeSpec(name="cam_generator", agent="noop", after=["risk_assessment"]),
        ],
    )


def _ran(state: dict) -> list[str]:
    return [f["node"] for f in state.get("findings", [])]


async def _demo() -> int:
    engine = create_async_engine(os.environ["IC_DATABASE__URL"])
    factory = async_sessionmaker(engine, expire_on_commit=False)
    reg = AgentRegistry()
    reg.register("noop")(NoopAgent)

    async with factory() as session:
        s = uuid.uuid4().hex[:8]
        org = Organization(name=f"Chaos {s}", slug=f"chaos-{s}", is_active=True)
        session.add(org)
        await session.flush()
        company = Company(
            org_id=org.id, legal_name=f"Acme {s}", data_origin=DataOrigin.SYNTHETIC_DEMO
        )
        session.add(company)
        await session.flush()
        case = LoanApplication(org_id=org.id, company_id=company.id, title="Chaos demo")
        session.add(case)
        await session.commit()
        org_id, case_id = org.id, case.id

    runner = WorkflowRunner(factory, agents=reg)
    definition = _definition()
    run_id = str(uuid.uuid4())
    state = initial_state(run_id, "chaos_demo")

    print("\n=== IntelliCredit chaos: kill-and-resume ===")
    print(f"run_id = {run_id}\n")

    print("[worker A] starting workflow ...")
    async with build_checkpointer() as saver_a:
        started = await runner.start(
            definition, state, org_id=org_id, loan_application_id=case_id, checkpointer=saver_a
        )
    print(f"  ran nodes: {_ran(started.state)}")
    print(f"  status: {started.status.value} (paused at the human-review gate)")
    assert started.status is WorkflowExecutionStatus.PAUSED_HITL

    print("\n[KILL] worker A is terminated mid-run (checkpointer closed).")
    print("       state survives only in the Postgres checkpoint.\n")

    print("[worker B] a FRESH worker resumes from the checkpoint ...")
    async with build_checkpointer() as saver_b:
        resumed = await runner.resume(definition, thread_id=run_id, checkpointer=saver_b)
    print(f"  ran nodes now: {_ran(resumed.state)}")
    print(f"  status: {resumed.status.value}")
    assert resumed.status is WorkflowExecutionStatus.COMPLETED

    async with factory() as session:
        rows = list(
            (
                await session.execute(
                    sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
                )
            ).scalars()
        )

    ran = _ran(resumed.state)
    dupes = {n for n in ran if ran.count(n) > 1}
    print("\n=== verification ===")
    print(f"  execution rows for this run : {len(rows)} (expected 1)")
    print(f"  nodes executed more than once: {dupes or 'none'}")
    ok = len(rows) == 1 and not dupes and rows[0].status is WorkflowExecutionStatus.COMPLETED
    print(f"\n  RESULT: {'PASS — durable resume, no duplication' if ok else 'FAIL'}")
    await engine.dispose()
    return 0 if ok else 1


def main() -> int:
    loop = (
        asyncio.SelectorEventLoop(selectors.SelectSelector())
        if sys.platform == "win32"
        else asyncio.new_event_loop()
    )
    try:
        return loop.run_until_complete(_demo())
    finally:
        loop.close()


if __name__ == "__main__":
    raise SystemExit(main())
