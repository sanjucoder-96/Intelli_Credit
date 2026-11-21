"""Workflow engine skeleton — workflows are data (ADR-0008).

YAML definitions are validated into :class:`WorkflowDefinition`, registered
as immutable versions, and compiled into LangGraph state graphs by the one
module allowed to touch the LangGraph builder. Phase 5 adds DB-backed
registration (``workflows`` table), the Postgres checkpointer wiring, HITL
resume endpoints, and the first real definition (``credit_appraisal``).
"""

from app.workflows.compiler import compile_workflow
from app.workflows.definition import AgentBinding, DashboardBlock, NodeSpec, WorkflowDefinition
from app.workflows.executions import ExecutionHandle, ExecutionRegistry
from app.workflows.loader import load_definition, load_directory
from app.workflows.registry import WorkflowRegistry, workflow_registry
from app.workflows.state import RunState

__all__ = [
    "AgentBinding",
    "DashboardBlock",
    "ExecutionHandle",
    "ExecutionRegistry",
    "NodeSpec",
    "RunState",
    "WorkflowDefinition",
    "WorkflowRegistry",
    "compile_workflow",
    "load_definition",
    "load_directory",
    "workflow_registry",
]
