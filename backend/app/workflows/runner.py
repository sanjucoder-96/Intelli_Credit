"""WorkflowRunner — durable execution over the Postgres checkpointer (S3.2–3.5).

Owns the run lifecycle end to end: persist the immutable definition row,
create the ``workflow_executions`` row (thread_id = run id), compile the graph
with a checkpointer, invoke it, and record the resulting lifecycle state
(PAUSED_HITL when an interrupt gate is pending, COMPLETED otherwise, FAILED on
error). ``resume`` re-enters a paused run — optionally injecting human input
into the checkpointed state first (HITL) — and LangGraph replays only the
pending nodes, so completed work is never re-executed and its trace/DB writes
are not duplicated.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

import app.agents.credit  # noqa: F401 — registers the credit-appraisal agents
from app.agents.registry import AgentRegistry, agent_registry
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.core.metrics import observe_workflow
from app.domain.enums import WorkflowExecutionStatus
from app.infrastructure.db.models import WorkflowExecution
from app.workflows.compiler import ContextFactory, compile_workflow
from app.workflows.definition import WorkflowDefinition
from app.workflows.persistence import create_execution, ensure_workflow_row, update_execution
from app.workflows.predicates import PredicateRegistry, predicate_registry
from app.workflows.state import RunState

logger = get_logger("app.workflows.runner")

# A builder given the freshly-created execution id, returning the per-run
# context factory. Real runs need the execution id (to tag traces + the LLM
# ledger) before the context exists, so callers pass a builder rather than a
# ready factory. ``compile_workflow`` falls back to a default factory when None.
ContextFactoryBuilder = Callable[[UUID], ContextFactory]


@dataclass
class RunOutcome:
    """The result of a start/resume: lifecycle status + the run state."""

    run_id: str
    execution_id: UUID
    status: WorkflowExecutionStatus
    state: dict[str, Any]
    interrupted: bool


class WorkflowRunner:
    """Runs and resumes workflows with durable persistence + checkpoints."""

    def __init__(
        self,
        session_factory: async_sessionmaker[Any],
        *,
        agents: AgentRegistry | None = None,
        predicates: PredicateRegistry | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._agents = agents or agent_registry
        self._predicates = predicates or predicate_registry

    async def start(
        self,
        definition: WorkflowDefinition,
        initial_state: RunState,
        *,
        org_id: UUID,
        checkpointer: Any,
        context_factory: ContextFactory | None = None,
        context_factory_builder: ContextFactoryBuilder | None = None,
        loan_application_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> RunOutcome:
        """Persist the definition + execution rows, then run the graph."""
        run_id = initial_state["run_id"]
        async with self._session_factory() as session:
            workflow_row = await ensure_workflow_row(session, definition)
            execution = await create_execution(
                session,
                org_id=org_id,
                workflow_id=workflow_row.id,
                thread_id=run_id,
                loan_application_id=loan_application_id,
                created_by=created_by,
            )
            execution_id = execution.id
            await session.commit()
        logger.info("workflow_run_started", run_id=run_id, execution_id=str(execution_id))
        factory = self._resolve_factory(context_factory, context_factory_builder, execution_id)
        return await self._invoke(
            definition, execution_id, run_id, initial_state, checkpointer, factory
        )

    async def resume(
        self,
        definition: WorkflowDefinition,
        *,
        thread_id: str,
        checkpointer: Any,
        resume_state: dict[str, Any] | None = None,
        context_factory: ContextFactory | None = None,
        context_factory_builder: ContextFactoryBuilder | None = None,
    ) -> RunOutcome:
        """Re-enter a paused run; optionally inject human input first (HITL)."""
        async with self._session_factory() as session:
            execution = (
                await session.execute(
                    sa.select(WorkflowExecution).where(WorkflowExecution.thread_id == thread_id)
                )
            ).scalar_one_or_none()
        if execution is None:
            raise NotFoundError(f"No workflow execution for thread '{thread_id}'")
        logger.info("workflow_run_resumed", run_id=thread_id, execution_id=str(execution.id))
        factory = self._resolve_factory(context_factory, context_factory_builder, execution.id)
        return await self._invoke(
            definition, execution.id, thread_id, None, checkpointer, factory, resume_state
        )

    @staticmethod
    def _resolve_factory(
        context_factory: ContextFactory | None,
        builder: ContextFactoryBuilder | None,
        execution_id: UUID,
    ) -> ContextFactory | None:
        """Prefer an explicit factory; else build one from the execution id."""
        if context_factory is not None:
            return context_factory
        if builder is not None:
            return builder(execution_id)
        return None

    async def _invoke(
        self,
        definition: WorkflowDefinition,
        execution_id: UUID,
        run_id: str,
        graph_input: RunState | None,
        checkpointer: Any,
        context_factory: ContextFactory | None,
        resume_state: dict[str, Any] | None = None,
    ) -> RunOutcome:
        graph = compile_workflow(
            definition,
            self._agents,
            checkpointer,
            predicates=self._predicates,
            context_factory=context_factory,
        )
        config: Any = {"configurable": {"thread_id": run_id}}
        interrupts = {node.name for node in definition.nodes if node.interrupt}
        _started = time.perf_counter()
        try:
            existing = await graph.aget_state(config)
            has_checkpoint = existing.created_at is not None
            pending_interrupt = bool(set(existing.next) & interrupts)
            if resume_state is not None:
                # HITL input: inject it as the interrupted (pending) node so the
                # write is unambiguous at a fan-in gate, then replay the still-
                # pending nodes. The gate is a noop pause point, so attributing
                # the update to it advances the run past the gate downstream.
                as_node = existing.next[0] if existing.next else None
                await graph.aupdate_state(config, resume_state, as_node=as_node)
                final = await graph.ainvoke(None, config=config)
            elif graph_input is None:
                # Explicit resume() (human continuation): advance from the
                # checkpoint, stepping past the HITL gate.
                final = await graph.ainvoke(None, config=config)
            elif has_checkpoint and pending_interrupt:
                # A start() redelivered by Celery (acks_late) while the run is
                # legitimately paused at a HITL gate: report the paused state
                # WITHOUT advancing — the human's decision point is not to be
                # stepped over by a mere redelivery.
                logger.info("workflow_start_redelivered_paused", run_id=run_id)
                final = dict(existing.values)
            elif has_checkpoint:
                # A start() redelivered after a mid-run crash (pending work, no
                # HITL gate): resume from the checkpoint rather than re-applying
                # the initial input, which the reducer channels (findings, cost)
                # would otherwise double-count. Exactly-once is a property of the
                # checkpoint, not of delivery.
                logger.info("workflow_start_idempotent_resume", run_id=run_id)
                final = await graph.ainvoke(None, config=config)
            else:
                final = await graph.ainvoke(graph_input, config=config)
        except Exception as exc:
            async with self._session_factory() as session:
                await update_execution(
                    session,
                    execution_id,
                    status=WorkflowExecutionStatus.FAILED,
                    error={"message": str(exc)[:500], "type": type(exc).__name__},
                )
                await session.commit()
            logger.error("workflow_run_failed", run_id=run_id, error=str(exc))
            observe_workflow(definition.key, "failed", time.perf_counter() - _started)
            raise

        snapshot = await graph.aget_state(config)
        interrupted = bool(snapshot.next)
        status = (
            WorkflowExecutionStatus.PAUSED_HITL
            if interrupted
            else WorkflowExecutionStatus.COMPLETED
        )
        state = dict(final)
        cost = float(state.get("cost_usd", 0.0) or 0.0)
        async with self._session_factory() as session:
            await update_execution(session, execution_id, status=status, cost_usd=cost)
            await session.commit()
        logger.info(
            "workflow_run_settled",
            run_id=run_id,
            status=status.value,
            interrupted=interrupted,
            findings=len(state.get("findings", [])),
        )
        observe_workflow(definition.key, status.value, time.perf_counter() - _started)
        return RunOutcome(
            run_id=run_id,
            execution_id=execution_id,
            status=status,
            state=state,
            interrupted=interrupted,
        )
