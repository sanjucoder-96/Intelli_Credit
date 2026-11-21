"""Workflow bootstrap — register agents + built-in definitions once per process.

Both entrypoints (the FastAPI app and the Celery worker) call
:func:`load_workflows` at startup so ``workflow_registry`` can resolve a
definition by key. Importing :mod:`app.agents.credit` registers the
credit-appraisal agents on the shared agent registry; the registry then
validates every definition's agent bindings as it loads them. Registration is
idempotent (identical ``(key, version)`` content is a no-op), so calling this
repeatedly — or from multiple workers — is safe.
"""

from __future__ import annotations

from pathlib import Path

import app.agents.credit  # noqa: F401 — registers the credit-appraisal agents
from app.core.logging import get_logger
from app.workflows.registry import WorkflowRegistry, workflow_registry

logger = get_logger("app.workflows.bootstrap")

DEFINITIONS_DIR = Path(__file__).resolve().parent / "definitions"

_loaded = False


def load_workflows(registry: WorkflowRegistry | None = None, *, force: bool = False) -> int:
    """Load built-in workflow definitions into the registry (idempotent).

    Returns the number of definitions currently registered. The one-shot guard
    keeps repeated startup imports cheap; ``force`` re-scans the directory.
    """
    global _loaded
    target = registry or workflow_registry
    if _loaded and not force and target is workflow_registry:
        return len(target.keys())
    count = target.load_builtin(DEFINITIONS_DIR)
    if target is workflow_registry:
        _loaded = True
    logger.info("workflows_loaded", count=count, keys=target.keys())
    return count
