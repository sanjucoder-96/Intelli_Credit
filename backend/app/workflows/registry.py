"""WorkflowRegistry — versioned, checksummed workflow definitions (ADR-0008).

Phase 2 keeps registration in-process: definitions are validated against the
agent registry (every node's agent must resolve) and stored per (key, version)
with a content checksum. Phase 5 persists each registered version as an
immutable row in the ``workflows`` table (which already exists) and pins every
run to the exact version it executed.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.agents.registry import AgentRegistry, agent_registry
from app.core.errors import NotFoundError, ValidationFailedError
from app.workflows.definition import WorkflowDefinition
from app.workflows.loader import load_directory


class WorkflowRegistry:
    """In-process registry of validated workflow definitions."""

    def __init__(self) -> None:
        self._definitions: dict[str, dict[int, WorkflowDefinition]] = {}
        self._checksums: dict[tuple[str, int], str] = {}

    def register(self, definition: WorkflowDefinition, agents: AgentRegistry | None = None) -> str:
        """Validate agent bindings and store the definition; returns its checksum.

        Re-registering the same (key, version) with identical content is a
        no-op; with different content it raises — versions are immutable.
        """
        agents = agents or agent_registry
        known = set(agents.names())
        missing = sorted({node.agent for node in definition.nodes} - known)
        if missing:
            raise ValidationFailedError(
                f"Workflow '{definition.key}' references unregistered agents",
                details={"missing_agents": missing, "registered": sorted(known)},
            )

        checksum = hashlib.sha256(
            definition.model_dump_json(exclude_none=True).encode("utf-8")
        ).hexdigest()

        existing = self._checksums.get((definition.key, definition.version))
        if existing is not None and existing != checksum:
            raise ValidationFailedError(
                f"Workflow '{definition.key}' v{definition.version} is immutable; "
                "register a new version instead",
            )

        self._definitions.setdefault(definition.key, {})[definition.version] = definition
        self._checksums[(definition.key, definition.version)] = checksum
        return checksum

    def get(self, key: str, version: int | None = None) -> WorkflowDefinition:
        """Fetch a definition — the exact version, or the latest when omitted."""
        versions = self._definitions.get(key)
        if not versions:
            raise NotFoundError(f"Workflow '{key}' is not registered")
        if version is None:
            return versions[max(versions)]
        if version not in versions:
            raise NotFoundError(
                f"Workflow '{key}' has no version {version}",
                details={"available": sorted(versions)},
            )
        return versions[version]

    def keys(self) -> list[str]:
        """Sorted keys of every registered workflow."""
        return sorted(self._definitions)

    def load_builtin(self, directory: Path, agents: AgentRegistry | None = None) -> int:
        """Load and register every definition in ``directory``; returns the count."""
        definitions = load_directory(directory)
        for definition in definitions:
            self.register(definition, agents)
        return len(definitions)


workflow_registry = WorkflowRegistry()
"""Process-wide workflow registry (definitions dir is empty until Phase 5)."""
