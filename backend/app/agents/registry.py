"""AgentRegistry — decorator-registered agent implementations (ADR-0008).

One of the nine extension-surface registries: a new agent is a registered
class (or a configured ``GenericLLMAgent`` instance in Phase 5), never a
routing change. The module-level :data:`agent_registry` is the process
singleton the workflow compiler resolves against.
"""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel

from app.agents.base import BaseAgent, Plan
from app.agents.context import AgentContext
from app.agents.spec import AgentSpec
from app.core.errors import NotFoundError

AgentFactory = Callable[[AgentSpec], BaseAgent]


class AgentRegistry:
    """Maps agent names to factories — a new agent is a registration, not a
    routing change (ADR-0008). Most Phase-5 agents are configured
    ``GenericLLMAgent`` instances registered via :meth:`register_factory`;
    deterministic agents register a class via the :meth:`register` decorator.
    """

    def __init__(self) -> None:
        self._entries: dict[str, tuple[AgentFactory, AgentSpec]] = {}

    def register(
        self, name: str, spec: AgentSpec | None = None
    ) -> Callable[[type[BaseAgent]], type[BaseAgent]]:
        """Class decorator: ``@agent_registry.register("financial_analysis")``."""

        def decorator(cls: type[BaseAgent]) -> type[BaseAgent]:
            self._entries[name] = (lambda s: cls(s), spec or AgentSpec(name=name))
            return cls

        return decorator

    def register_factory(
        self, name: str, factory: AgentFactory, default_spec: AgentSpec | None = None
    ) -> None:
        """Register a configured-instance factory (GenericLLMAgent packs)."""
        self._entries[name] = (factory, default_spec or AgentSpec(name=name))

    def create(self, name: str, spec: AgentSpec | None = None) -> BaseAgent:
        """Instantiate a registered agent, optionally with a spec override."""
        entry = self._entries.get(name)
        if entry is None:
            raise NotFoundError(
                f"Agent '{name}' is not registered",
                details={"registered": sorted(self._entries)},
            )
        factory, default_spec = entry
        return factory(spec or default_spec)

    def spec_for(self, name: str) -> AgentSpec:
        """The registered default spec for a name (tool whitelist, budget)."""
        entry = self._entries.get(name)
        if entry is None:
            raise NotFoundError(f"Agent '{name}' is not registered")
        return entry[1]

    def names(self) -> list[str]:
        """Sorted names of every registered agent."""
        return sorted(self._entries)


agent_registry = AgentRegistry()
"""Process-wide agent registry; workflow definitions resolve against it."""


class NoopOutput(BaseModel):
    """Output of the no-op agent."""

    note: str = "noop"


@agent_registry.register("noop")
class NoopAgent(BaseAgent):
    """Does nothing and claims nothing — exists to exercise the runtime.

    Used by compiler/runtime tests and as the reference for how little an
    agent implementation needs to provide. It is NOT a mock of any real
    capability.
    """

    async def act(self, ctx: AgentContext, plan: Plan) -> NoopOutput:
        """Return the fixed no-op output."""
        return NoopOutput()
