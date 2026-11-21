"""WorkflowDefinition — the declarative workflow DSL (ADR-0008).

A workflow is data: YAML-authored, Pydantic-validated fail-fast, registered
as an immutable version, compiled to a LangGraph graph. The DSL covers nodes,
DAG edges (``after``), conditional edges (``route`` → a deterministic
predicate + label→node targets), dynamic fan-out/fan-in (``fan_out`` → a
LangGraph ``Send`` over a state list into a worker node), HITL interrupt
markers, per-agent tool whitelists/budgets, and the server-driven dashboard
block vocabulary. The DSL is frozen only after three real workflows shape it
(harvest-the-abstraction rule).
"""

from __future__ import annotations

from graphlib import CycleError, TopologicalSorter
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.agents.spec import StepBudget

DashboardBlockType = Literal[
    "risk_gauge",
    "table",
    "timeline",
    "heatmap",
    "network",
    "narrative",
    "evidence_chain",
]
"""The fixed block vocabulary the frontend renders (ARCHITECTURE §4)."""


class DashboardBlock(BaseModel):
    """A typed instance of the fixed dashboard block vocabulary."""

    type: DashboardBlockType
    id: str | None = None


class AgentBinding(BaseModel):
    """Per-workflow agent configuration: tool whitelist and budget override."""

    tools: list[str] = Field(default_factory=list)
    budget: StepBudget | None = None


END_TARGET = "END"
"""Sentinel a route target may name to terminate the graph."""


class RouteSpec(BaseModel):
    """Conditional-edge routing from a node.

    ``predicate`` names a deterministic state-only function in the
    PredicateRegistry; ``targets`` maps each label it can return to the next
    node's name (or ``"END"``). Compiles to ``add_conditional_edges`` with a
    path map — so a routed node's outgoing edges are its route targets, not
    its ``after`` successors.
    """

    predicate: str
    targets: dict[str, str] = Field(min_length=1)


class FanOutSpec(BaseModel):
    """Dynamic fan-out over a state list via LangGraph ``Send``.

    ``over`` is a run-state list key (top-level, e.g. ``documents``, or
    ``scratch.<key>``); one ``Send`` per item is dispatched to the ``worker``
    node, each with its own per-item state. Fan-in is automatic: nodes that
    list the fan-out node in ``after`` receive an edge from the worker, so
    they run once all workers have merged their deltas.
    """

    over: str
    worker: str


class NodeSpec(BaseModel):
    """One node of the workflow graph."""

    name: str
    agent: str
    required: bool = True
    after: list[str] = Field(default_factory=list)
    interrupt: bool = False
    route: RouteSpec | None = None
    fan_out: FanOutSpec | None = None


class WorkflowDefinition(BaseModel):
    """A complete, validated workflow definition."""

    key: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    entity_type: str = "corporate"
    nodes: list[NodeSpec] = Field(min_length=1)
    agents: dict[str, AgentBinding] = Field(default_factory=dict)
    output_schema: str | None = None
    dashboard: list[DashboardBlock] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_graph(self) -> WorkflowDefinition:
        """Node names unique; every edge resolves; the graph is acyclic."""
        names = [node.name for node in self.nodes]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f"Duplicate node names: {sorted(duplicates)}")

        known = set(names)
        for node in self.nodes:
            unknown = [parent for parent in node.after if parent not in known]
            if unknown:
                raise ValueError(f"Node '{node.name}' references unknown nodes: {unknown}")
            if node.route is not None:
                bad = [
                    target
                    for target in node.route.targets.values()
                    if target != END_TARGET and target not in known
                ]
                if bad:
                    raise ValueError(f"Node '{node.name}' route targets unknown nodes: {bad}")
                if node.fan_out is not None:
                    raise ValueError(f"Node '{node.name}' cannot have both route and fan_out")
            if node.fan_out is not None and node.fan_out.worker not in known:
                raise ValueError(
                    f"Node '{node.name}' fan_out worker '{node.fan_out.worker}' is not a node"
                )

        # Acyclicity is checked over the unconditional ``after`` DAG only;
        # conditional/route/fan_out edges are runtime-bounded by LangGraph's
        # recursion limit (a routed loop is a legitimate, if rare, pattern).
        try:
            TopologicalSorter({node.name: set(node.after) for node in self.nodes}).prepare()
        except CycleError as exc:
            raise ValueError(f"Workflow graph contains a cycle: {exc.args[1]}") from exc
        return self
