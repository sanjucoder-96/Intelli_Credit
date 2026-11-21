"""GraphCompiler — the ONLY module that touches LangGraph's builder (ADR-0008).

DSL → builder mapping:
- ``NodeSpec``                → ``add_node`` (an adapter wraps ``BaseAgent.run``
  into a state-delta function)
- empty ``after`` (non-target) → edge from START
- each ``after`` entry         → ``add_edge(parent, node)`` (siblings run in
  parallel; multiple parents form a fan-in join)
- ``route``                    → ``add_conditional_edges(node, predicate, map)``;
  a routed node's outgoing edges are its route targets, not ``after``
- ``fan_out``                  → ``add_conditional_edges`` returning one
  ``Send(worker, per-item state)`` per item; fan-in is the worker → the
  fan-out node's ``after`` successors
- ``interrupt: true``          → ``compile(interrupt_before=[...])`` (HITL gate;
  requires a checkpointer at run time to pause/resume)

The ``context_factory`` seam lets a later slice inject the real agent runtime
(LLM caller, tool belt, DB session, cost governor, DB-backed trace) without
touching this topology code — Phase-5 slice 3 keeps the Phase-2 bare context
as the default so agent/tool abstractions and layering are preserved.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Hashable
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Send

from app.agents.budget import BudgetMeter
from app.agents.context import AgentContext, LoggingTraceWriter
from app.agents.registry import AgentRegistry, agent_registry
from app.core.logging import get_logger
from app.workflows.definition import END_TARGET, NodeSpec, WorkflowDefinition
from app.workflows.predicates import PredicateRegistry, predicate_registry
from app.workflows.state import RunState

logger = get_logger("app.workflows.compiler")

NodeFunction = Callable[[RunState], Awaitable[dict[str, Any]]]
ContextFactory = Callable[[NodeSpec, RunState], AgentContext]

CompiledWorkflow = CompiledStateGraph[RunState, None, RunState, RunState]
"""Concrete compiled-graph type for the shared RunState shape."""


def _read_list(state: RunState, over: str) -> list[Any]:
    """Read the fan-out source list from a top-level or ``scratch.<k>`` key."""
    data: dict[str, Any] = dict(state)
    if over.startswith("scratch."):
        value = data.get("scratch", {}).get(over.split(".", 1)[1], [])
    else:
        value = data.get(over, [])
    return list(value) if isinstance(value, list) else []


def _worker_state(state: RunState, item: Any) -> dict[str, Any]:
    """Per-item state for one ``Send`` worker invocation.

    Each worker gets a fresh append-only findings/errors channel (they merge
    back into the run via the reducers) and the item under ``scratch.fan_item``.
    """
    return {
        "run_id": state["run_id"],
        "workflow_key": state["workflow_key"],
        "documents": state.get("documents", []),
        "findings": [],
        "errors": [],
        "scratch": {**state.get("scratch", {}), "fan_item": item},
    }


def _default_context_factory(
    definition: WorkflowDefinition, agents: AgentRegistry
) -> ContextFactory:
    """The Phase-2 bare context: logging trace + per-node budget, no LLM/tools.

    Later slices replace this with a factory that injects the caller, tool
    belt, DB session, cost governor, and DB-backed trace writer.
    """

    def factory(node: NodeSpec, state: RunState) -> AgentContext:
        binding = definition.agents.get(node.agent)
        default_spec = agents.spec_for(node.agent)
        budget_spec = (
            binding.budget if binding is not None and binding.budget else default_spec.budget
        )
        return AgentContext(
            run_id=state["run_id"],
            workflow_key=state["workflow_key"],
            node=node.name,
            state=dict(state),
            trace=LoggingTraceWriter(),
            budget=BudgetMeter(budget_spec),
        )

    return factory


def _make_node(node: NodeSpec, agents: AgentRegistry, factory: ContextFactory) -> NodeFunction:
    """Wrap an agent's lifecycle into a LangGraph node returning a state delta."""
    agent = agents.create(node.agent)

    async def run_node(state: RunState) -> dict[str, Any]:
        ctx = factory(node, state)
        try:
            result = await agent.run(ctx)
        except Exception as exc:
            if node.required:
                raise
            # Degraded parking (ARCHITECTURE §5): a non-required node's failure
            # becomes a recorded gap, not a dead run.
            logger.warning(
                "workflow_node_degraded", node=node.name, agent=node.agent, error=str(exc)
            )
            return {"errors": [{"node": node.name, "agent": node.agent, "error": str(exc)}]}

        output = result.output
        payload = output.model_dump() if hasattr(output, "model_dump") else output
        delta: dict[str, Any] = {
            "findings": [
                {
                    "node": node.name,
                    "agent": node.agent,
                    "confidence": result.confidence.score,
                    "output": payload,
                }
            ]
        }
        # Accumulate this node's LLM spend into the run's cost channel so the
        # per-run cost governor sees the running total across nodes.
        if ctx.governor is not None and ctx.governor.node_spent:
            delta["cost_usd"] = ctx.governor.node_spent
        return delta

    return run_node


def _make_dispatch(node: NodeSpec, fan_in: list[str]) -> Callable[[RunState], list[Any]]:
    """Build the fan-out path function.

    Non-empty list → one ``Send(worker, item-state)`` per item (fan-in runs via
    the worker → successor edges). Empty list → route straight to the fan-in
    successors (or END) so the stage after the fan-out always runs, even with
    zero items.
    """
    assert node.fan_out is not None
    worker = node.fan_out.worker
    over = node.fan_out.over

    def dispatch(state: RunState) -> list[Any]:
        items = _read_list(state, over)
        if not items:
            return list(fan_in) if fan_in else [END]
        return [Send(worker, _worker_state(state, item)) for item in items]

    return dispatch


def compile_workflow(
    definition: WorkflowDefinition,
    agents: AgentRegistry | None = None,
    checkpointer: Any = None,
    *,
    predicates: PredicateRegistry | None = None,
    context_factory: ContextFactory | None = None,
) -> CompiledWorkflow:
    """Compile a validated definition into an executable LangGraph graph."""
    agents = agents or agent_registry
    predicates = predicates or predicate_registry
    factory = context_factory or _default_context_factory(definition, agents)

    builder = StateGraph(RunState)
    for node in definition.nodes:
        # LangGraph accepts partial-update dicts from nodes at runtime; its
        # node typing wants the full state shape, hence the narrow ignore.
        builder.add_node(node.name, _make_node(node, agents, factory))  # type: ignore[call-overload]

    worker_names = {n.fan_out.worker for n in definition.nodes if n.fan_out is not None}
    route_targets: set[str] = {
        target
        for n in definition.nodes
        if n.route is not None
        for target in n.route.targets.values()
        if target != END_TARGET
    }
    reached_special = worker_names | route_targets
    successors: dict[str, list[str]] = {}
    for node in definition.nodes:
        for parent in node.after:
            successors.setdefault(parent, []).append(node.name)

    # Outgoing edges.
    for node in definition.nodes:
        if node.fan_out is not None:
            worker = node.fan_out.worker
            fan_in = successors.get(node.name, [])
            builder.add_conditional_edges(node.name, _make_dispatch(node, fan_in))
            for target in fan_in or [END]:
                builder.add_edge(worker, target)
        elif node.route is not None:
            path_map: dict[Hashable, str] = {
                label: (END if target == END_TARGET else target)
                for label, target in node.route.targets.items()
            }
            builder.add_conditional_edges(node.name, predicates.get(node.route.predicate), path_map)
        else:
            children = successors.get(node.name, [])
            if children:
                for child in children:
                    builder.add_edge(node.name, child)
            elif node.name not in worker_names:
                builder.add_edge(node.name, END)

    # START edges: source nodes with no unconditional parent, never workers.
    for node in definition.nodes:
        if node.name in worker_names:
            continue
        if not node.after and node.name not in reached_special:
            builder.add_edge(START, node.name)

    interrupts = [node.name for node in definition.nodes if node.interrupt]
    compiled: CompiledWorkflow = builder.compile(
        checkpointer=checkpointer,
        interrupt_before=interrupts or None,
    )
    logger.info(
        "workflow_compiled",
        key=definition.key,
        version=definition.version,
        nodes=len(definition.nodes),
        interrupts=interrupts,
        fan_out=sorted(worker_names),
    )
    return compiled
