"""ExecutionRegistry — tracks workflow runs (Phase-2: in-process).

Phase 5 persists every run as a ``workflow_executions`` row (the table exists),
wires the Postgres checkpointer with ``thread_id = run_id``, and moves
execution onto Celery workers with checkpoint-resume semantics
(ARCHITECTURE §15.3). The handle shape is stable across that move.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Typing-only: RunnableConfig is LangGraph's own config type, re-exported
    # from its langchain-core dependency (ADR-0003 allows langchain-core as a
    # transitive dependency; no runtime import happens here).
    from langchain_core.runnables import RunnableConfig

from app.agents.registry import AgentRegistry, agent_registry
from app.core.errors import NotFoundError
from app.core.logging import get_logger
from app.domain.enums import WorkflowExecutionStatus
from app.workflows.compiler import compile_workflow
from app.workflows.definition import WorkflowDefinition
from app.workflows.state import RunState, initial_state

logger = get_logger("app.workflows.executions")


@dataclass
class ExecutionHandle:
    """Mutable status record for one workflow run."""

    run_id: str
    workflow_key: str
    workflow_version: int
    status: WorkflowExecutionStatus
    result: dict[str, Any] | None = field(default=None)
    error: str | None = field(default=None)


class ExecutionRegistry:
    """Starts runs and answers status queries."""

    def __init__(self) -> None:
        self._handles: dict[str, ExecutionHandle] = {}

    async def start(
        self,
        definition: WorkflowDefinition,
        state: RunState | None = None,
        *,
        agents: AgentRegistry | None = None,
        checkpointer: Any = None,
    ) -> ExecutionHandle:
        """Compile and run a workflow to completion; returns its handle.

        With a checkpointer the invocation carries ``thread_id = run_id`` so
        the run is durable/resumable; without one it executes in-memory
        (sufficient for Phase-2 runtime tests).
        """
        run_state = state or initial_state(str(uuid.uuid4()), definition.key)
        run_id = run_state["run_id"]
        handle = ExecutionHandle(
            run_id=run_id,
            workflow_key=definition.key,
            workflow_version=definition.version,
            status=WorkflowExecutionStatus.RUNNING,
        )
        self._handles[run_id] = handle

        graph = compile_workflow(definition, agents or agent_registry, checkpointer)
        config: RunnableConfig | None = (
            {"configurable": {"thread_id": run_id}} if checkpointer is not None else None
        )
        # (annotation is typing-only; see the TYPE_CHECKING import above)

        logger.info("workflow_run_started", run_id=run_id, workflow=definition.key)
        try:
            final_state = await graph.ainvoke(run_state, config=config)
        except Exception as exc:
            handle.status = WorkflowExecutionStatus.FAILED
            handle.error = str(exc)
            logger.error("workflow_run_failed", run_id=run_id, error=str(exc))
            raise

        handle.status = WorkflowExecutionStatus.COMPLETED
        handle.result = dict(final_state)
        logger.info(
            "workflow_run_completed",
            run_id=run_id,
            workflow=definition.key,
            findings=len(handle.result.get("findings", [])),
            errors=len(handle.result.get("errors", [])),
        )
        return handle

    def status(self, run_id: str) -> ExecutionHandle:
        """Return the handle for ``run_id`` or raise :class:`NotFoundError`."""
        handle = self._handles.get(run_id)
        if handle is None:
            raise NotFoundError(f"No workflow run with id '{run_id}'")
        return handle
