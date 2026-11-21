"""PredicateRegistry — deterministic, state-only routing (ADR-0008).

The ninth extension registry. A predicate is a pure function of the run
state that returns a label; the compiler maps that label to the next node via
the node's ``route.targets``. Routing is therefore deterministic and
inspectable — it never asks an LLM which way to go (invariant #2: numbers and
control flow come from deterministic code). New workflows register new
predicates; the compiler and runtime are untouched.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.errors import NotFoundError
from app.workflows.state import RunState

Predicate = Callable[[RunState], str]


class PredicateRegistry:
    """Maps predicate names to state-only routing functions."""

    def __init__(self) -> None:
        self._predicates: dict[str, Predicate] = {}

    def register(self, name: str) -> Callable[[Predicate], Predicate]:
        """Decorator: ``@predicate_registry.register("route_on_review")``."""

        def decorator(fn: Predicate) -> Predicate:
            self._predicates[name] = fn
            return fn

        return decorator

    def register_predicate(self, name: str, fn: Predicate) -> None:
        """Imperative registration (tests, dynamic packs)."""
        self._predicates[name] = fn

    def get(self, name: str) -> Predicate:
        predicate = self._predicates.get(name)
        if predicate is None:
            raise NotFoundError(
                f"Predicate '{name}' is not registered",
                details={"registered": sorted(self._predicates)},
            )
        return predicate

    def has(self, name: str) -> bool:
        return name in self._predicates

    def names(self) -> list[str]:
        return sorted(self._predicates)


predicate_registry = PredicateRegistry()
"""Process-wide predicate registry; workflow routes resolve against it."""


@predicate_registry.register("has_findings")
def _has_findings(state: RunState) -> str:
    """Reusable example: 'yes' when the run has produced any findings."""
    return "yes" if state.get("findings") else "no"
