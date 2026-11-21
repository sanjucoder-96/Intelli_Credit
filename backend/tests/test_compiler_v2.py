"""GraphCompiler v2 (Slice 3.1): conditional edges, Send fan-out/fan-in,
interrupt pause+resume. Topology only — the no-op agent stands in for real
agents, and the in-memory checkpointer avoids the Postgres dependency (that
saver lands in S3.2).
"""

from __future__ import annotations

import pytest
from langgraph.checkpoint.memory import MemorySaver

from app.core.errors import NotFoundError
from app.workflows.compiler import compile_workflow
from app.workflows.definition import FanOutSpec, NodeSpec, RouteSpec, WorkflowDefinition
from app.workflows.predicates import PredicateRegistry
from app.workflows.state import RunState, initial_state


def _state(key: str, **scratch: object) -> RunState:
    state = initial_state("run-x", key)
    state["scratch"] = dict(scratch)
    return state


def _nodes_run(final: RunState) -> list[str]:
    return [finding["node"] for finding in final["findings"]]


async def test_conditional_route_picks_branch_from_state() -> None:
    predicates = PredicateRegistry()
    predicates.register_predicate("pick", lambda s: str(s["scratch"].get("choice", "a")))

    definition = WorkflowDefinition(
        key="routed",
        version=1,
        nodes=[
            NodeSpec(
                name="gate",
                agent="noop",
                route=RouteSpec(predicate="pick", targets={"a": "approve", "b": "reject"}),
            ),
            NodeSpec(name="approve", agent="noop"),
            NodeSpec(name="reject", agent="noop"),
        ],
    )
    graph = compile_workflow(definition, predicates=predicates)

    took_a = await graph.ainvoke(_state("routed", choice="a"))
    assert set(_nodes_run(took_a)) == {"gate", "approve"}

    took_b = await graph.ainvoke(_state("routed", choice="b"))
    assert set(_nodes_run(took_b)) == {"gate", "reject"}


async def test_route_to_end_terminates() -> None:
    predicates = PredicateRegistry()
    predicates.register_predicate("stop", lambda s: "done")
    definition = WorkflowDefinition(
        key="route_end",
        version=1,
        nodes=[
            NodeSpec(
                name="gate",
                agent="noop",
                route=RouteSpec(predicate="stop", targets={"done": "END", "go": "after"}),
            ),
            NodeSpec(name="after", agent="noop"),
        ],
    )
    graph = compile_workflow(definition, predicates=predicates)
    final = await graph.ainvoke(_state("route_end"))
    assert _nodes_run(final) == ["gate"]  # 'after' never ran


async def test_send_fan_out_and_fan_in() -> None:
    definition = WorkflowDefinition(
        key="fanned",
        version=1,
        nodes=[
            NodeSpec(
                name="seed", agent="noop", fan_out=FanOutSpec(over="scratch.items", worker="worker")
            ),
            NodeSpec(name="worker", agent="noop"),
            NodeSpec(name="collect", agent="noop", after=["seed"]),
        ],
    )
    graph = compile_workflow(definition)
    final = await graph.ainvoke(_state("fanned", items=[1, 2, 3]))

    counts: dict[str, int] = {}
    for node in _nodes_run(final):
        counts[node] = counts.get(node, 0) + 1
    assert counts["seed"] == 1
    assert counts["worker"] == 3  # one Send per item
    assert counts["collect"] == 1  # fan-in ran once, after the workers


async def test_fan_out_over_empty_list_skips_workers() -> None:
    definition = WorkflowDefinition(
        key="empty_fan",
        version=1,
        nodes=[
            NodeSpec(
                name="seed", agent="noop", fan_out=FanOutSpec(over="scratch.items", worker="worker")
            ),
            NodeSpec(name="worker", agent="noop"),
            NodeSpec(name="collect", agent="noop", after=["seed"]),
        ],
    )
    graph = compile_workflow(definition)
    final = await graph.ainvoke(_state("empty_fan", items=[]))
    counts = {n: _nodes_run(final).count(n) for n in _nodes_run(final)}
    assert counts.get("worker", 0) == 0
    assert counts.get("collect", 0) == 1  # fan-in still runs


async def test_interrupt_pauses_then_resumes() -> None:
    definition = WorkflowDefinition(
        key="hitl",
        version=1,
        nodes=[
            NodeSpec(name="analyze", agent="noop"),
            NodeSpec(name="review", agent="noop", after=["analyze"], interrupt=True),
            NodeSpec(name="decide", agent="noop", after=["review"]),
        ],
    )
    saver = MemorySaver()
    graph = compile_workflow(definition, checkpointer=saver)
    config = {"configurable": {"thread_id": "run-x"}}

    paused = await graph.ainvoke(_state("hitl"), config=config)
    assert _nodes_run(paused) == ["analyze"]  # stopped before the interrupt node
    snapshot = await graph.aget_state(config)
    assert snapshot.next == ("review",)  # HITL gate is pending

    resumed = await graph.ainvoke(None, config=config)  # human input would go here (S3.4)
    assert set(_nodes_run(resumed)) == {"analyze", "review", "decide"}
    assert (await graph.aget_state(config)).next == ()  # completed


def test_route_targets_must_resolve() -> None:
    with pytest.raises(ValueError, match="route targets unknown nodes"):
        WorkflowDefinition(
            key="bad_route",
            version=1,
            nodes=[
                NodeSpec(
                    name="gate",
                    agent="noop",
                    route=RouteSpec(predicate="p", targets={"a": "ghost"}),
                )
            ],
        )


def test_fan_out_worker_must_be_a_node() -> None:
    with pytest.raises(ValueError, match="fan_out worker"):
        WorkflowDefinition(
            key="bad_fan",
            version=1,
            nodes=[
                NodeSpec(
                    name="seed", agent="noop", fan_out=FanOutSpec(over="documents", worker="ghost")
                )
            ],
        )


def test_route_and_fan_out_are_mutually_exclusive() -> None:
    with pytest.raises(ValueError, match="both route and fan_out"):
        WorkflowDefinition(
            key="both",
            version=1,
            nodes=[
                NodeSpec(
                    name="seed",
                    agent="noop",
                    route=RouteSpec(predicate="p", targets={"a": "w"}),
                    fan_out=FanOutSpec(over="documents", worker="w"),
                ),
                NodeSpec(name="w", agent="noop"),
            ],
        )


def test_unknown_predicate_fails_at_compile() -> None:
    definition = WorkflowDefinition(
        key="unknown_pred",
        version=1,
        nodes=[
            NodeSpec(
                name="gate",
                agent="noop",
                route=RouteSpec(predicate="nonexistent", targets={"a": "END"}),
            )
        ],
    )
    with pytest.raises(NotFoundError, match="Predicate 'nonexistent'"):
        compile_workflow(definition, predicates=PredicateRegistry())
