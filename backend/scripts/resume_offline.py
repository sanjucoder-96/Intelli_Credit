"""Complete a paused/failed credit_appraisal run offline (scripted LLM).

Used for the Phase-7 demo when live Gemini is unavailable: resumes a run from
its checkpoint using the deterministic engine + a scripted narrative caller, so
the officer's already-submitted HITL note flows through to a decision + CAM.

    ./.venv/Scripts/python.exe -m scripts.resume_offline <run_id>
"""

from __future__ import annotations

import sys
import uuid

import sqlalchemy as sa

import app.agents.credit  # noqa: F401
from app.core.config import get_settings
from app.core.context import TenantContext
from app.domain.enums import Role
from app.infrastructure.db.engine import get_session_factory
from app.infrastructure.db.models import WorkflowExecution
from app.infrastructure.ml.registry import clear_champion_cache
from app.workflows.bootstrap import load_workflows
from app.workflows.checkpointer import build_checkpointer
from app.workflows.registry import workflow_registry
from app.workflows.runner import WorkflowRunner
from app.workflows.runtime import build_runtime_bundle, make_context_factory
from scripts.provision_demo import ScriptedCaller, _run_async


async def _resume(run_id: str) -> None:
    load_workflows()
    clear_champion_cache()
    settings = get_settings()
    factory = get_session_factory()
    definition = workflow_registry.get("credit_appraisal")
    scripted = ScriptedCaller()

    async with factory() as session:
        execution = (
            await session.execute(
                sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == run_id)
            )
        ).scalar_one()
        org_id = execution.org_id
        case_id = execution.loan_application_id

    tenant = TenantContext(org_id=org_id, user_id=uuid.uuid4(), role=Role.ANALYST)

    def builder(execution_id):  # type: ignore[no-untyped-def]
        bundle = build_runtime_bundle(
            session_factory=factory, org_id=org_id, execution_id=execution_id, run_id=run_id,
            tenant=tenant, settings=settings, redis=None, case_id=case_id,
        )
        base = make_context_factory(bundle, definition)

        def factory_fn(node, state):  # type: ignore[no-untyped-def]
            ctx = base(node, state)
            ctx.caller = scripted
            return ctx

        return factory_fn

    runner = WorkflowRunner(factory)
    async with build_checkpointer() as saver:
        out = await runner.resume(
            definition, thread_id=run_id, checkpointer=saver, context_factory_builder=builder
        )
    print(f"{run_id}: {out.status.value}")


if __name__ == "__main__":
    _run_async(_resume(sys.argv[1]))
