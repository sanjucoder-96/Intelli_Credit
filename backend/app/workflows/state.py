"""Shared LangGraph run state (ARCHITECTURE §5: references, not blobs).

One state shape keeps the compiler, checkpointer, and trace UI generic across
all workflows. ``findings`` and ``errors`` are append-only channels with
``operator.add`` reducers so parallel branches merge safely. Phase 5 adds the
HITL sub-state and the run cost ledger; per-workflow scratch models validate
``scratch`` at the boundaries.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict


def _merge_scratch(current: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Merge scratch updates key-wise so a HITL injection (officer notes) does
    not clobber values set at run start (e.g. the assembled financials)."""
    return {**current, **update}


class RunState(TypedDict):
    """Working memory of one workflow run, checkpointed by LangGraph."""

    run_id: str
    workflow_key: str
    documents: list[str]
    findings: Annotated[list[dict[str, Any]], operator.add]
    errors: Annotated[list[dict[str, Any]], operator.add]
    cost_usd: Annotated[float, operator.add]
    scratch: Annotated[dict[str, Any], _merge_scratch]


def initial_state(run_id: str, workflow_key: str) -> RunState:
    """Construct a fresh, empty run state."""
    return RunState(
        run_id=run_id,
        workflow_key=workflow_key,
        documents=[],
        findings=[],
        errors=[],
        cost_usd=0.0,
        scratch={},
    )
