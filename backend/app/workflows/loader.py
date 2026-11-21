"""YAML loading for workflow definitions — validation is fail-fast (ADR-0008)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.core.errors import ValidationFailedError
from app.workflows.definition import WorkflowDefinition


def load_definition(path: Path) -> WorkflowDefinition:
    """Parse one YAML file into a validated :class:`WorkflowDefinition`.

    Raises :class:`ValidationFailedError` carrying the file path and every
    Pydantic error so a broken definition is obvious at startup, never at
    run time.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValidationFailedError(
            f"Workflow definition is not valid YAML: {path}",
            details={"error": str(exc)},
        ) from exc

    if not isinstance(raw, dict):
        raise ValidationFailedError(
            f"Workflow definition must be a mapping: {path}",
            details={"found": type(raw).__name__},
        )

    try:
        return WorkflowDefinition.model_validate(raw)
    except ValidationError as exc:
        raise ValidationFailedError(
            f"Invalid workflow definition: {path}",
            details={"errors": exc.errors(include_url=False)},
        ) from exc


def load_directory(directory: Path) -> list[WorkflowDefinition]:
    """Load every ``*.yaml``/``*.yml`` in ``directory`` (empty directory is fine)."""
    if not directory.exists():
        return []
    paths = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml")])
    return [load_definition(path) for path in paths]
