"""Compiling and executing workflow definitions end-to-end (no services)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.base import BaseAgent, Plan
from app.agents.context import AgentContext
from app.agents.registry import AgentRegistry, NoopOutput, agent_registry
from app.core.errors import NotFoundError
from app.domain.enums import WorkflowExecutionStatus
from app.workflows.compiler import compile_workflow
from app.workflows.definition import NodeSpec, WorkflowDefinition
from app.workflows.executions import ExecutionRegistry
from app.workflows.loader import load_definition
from app.workflows.registry import WorkflowRegistry
from app.workflows.state import initial_state

FIXTURES = Path(__file__).parent / "fixtures"


async def test_compile_and_run_smoke_workflow() -> None:
    definition = load_definition(FIXTURES / "wf_smoke.yaml")
    graph = compile_workflow(definition)

    final = await graph.ainvoke(initial_state("run-1", definition.key))

    nodes_run = [finding["node"] for finding in final["findings"]]
    assert nodes_run == ["gather", "summarize"]
    assert final["errors"] == []
    assert all(f["output"] == NoopOutput().model_dump() for f in final["findings"])
    assert all(0.0 <= f["confidence"] <= 1.0 for f in final["findings"])


async def test_parallel_siblings_both_run() -> None:
    definition = WorkflowDefinition(
        key="parallel",
        version=1,
        nodes=[
            NodeSpec(name="root", agent="noop"),
            NodeSpec(name="left", agent="noop", after=["root"]),
            NodeSpec(name="right", agent="noop", after=["root"]),
            NodeSpec(name="join", agent="noop", after=["left", "right"]),
        ],
    )
    graph = compile_workflow(definition)
    final = await graph.ainvoke(initial_state("run-2", definition.key))

    nodes_run = [finding["node"] for finding in final["findings"]]
    assert nodes_run[0] == "root"
    assert nodes_run[-1] == "join"
    assert {"left", "right"} == set(nodes_run[1:3])


async def test_non_required_node_failure_degrades_not_fails() -> None:
    registry = AgentRegistry()

    class ExplodingAgent(BaseAgent):
        async def act(self, ctx: AgentContext, plan: Plan) -> NoopOutput:
            raise RuntimeError("boom")

    registry.register("noop")(agent_registry.create("noop").__class__)
    registry.register("exploding")(ExplodingAgent)

    definition = WorkflowDefinition(
        key="degraded",
        version=1,
        nodes=[
            NodeSpec(name="fragile", agent="exploding", required=False),
            NodeSpec(name="steady", agent="noop", after=["fragile"]),
        ],
    )
    graph = compile_workflow(definition, registry)
    final = await graph.ainvoke(initial_state("run-3", definition.key))

    assert [e["node"] for e in final["errors"]] == ["fragile"]
    assert [f["node"] for f in final["findings"]] == ["steady"]


async def test_required_node_failure_raises() -> None:
    registry = AgentRegistry()

    class ExplodingAgent(BaseAgent):
        async def act(self, ctx: AgentContext, plan: Plan) -> NoopOutput:
            raise RuntimeError("boom")

    registry.register("exploding")(ExplodingAgent)

    definition = WorkflowDefinition(
        key="hard_fail",
        version=1,
        nodes=[NodeSpec(name="critical", agent="exploding", required=True)],
    )
    graph = compile_workflow(definition, registry)
    with pytest.raises(RuntimeError, match="boom"):
        await graph.ainvoke(initial_state("run-4", definition.key))


async def test_execution_registry_tracks_run() -> None:
    definition = load_definition(FIXTURES / "wf_smoke.yaml")
    executions = ExecutionRegistry()

    handle = await executions.start(definition)

    assert handle.status is WorkflowExecutionStatus.COMPLETED
    assert handle.workflow_key == "wf_smoke"
    assert handle.result is not None
    assert len(handle.result["findings"]) == 2
    assert executions.status(handle.run_id) is handle
    with pytest.raises(NotFoundError):
        executions.status("no-such-run")


def test_workflow_registry_register_get_and_immutability() -> None:
    definition = load_definition(FIXTURES / "wf_smoke.yaml")
    registry = WorkflowRegistry()

    checksum = registry.register(definition)
    assert registry.register(definition) == checksum  # identical re-register is a no-op
    assert registry.get("wf_smoke").version == 1
    assert registry.get("wf_smoke", 1).key == "wf_smoke"
    assert registry.keys() == ["wf_smoke"]

    mutated = definition.model_copy(update={"entity_type": "individual"})
    with pytest.raises(Exception, match="immutable"):
        registry.register(mutated)


def test_workflow_registry_rejects_unknown_agents() -> None:
    definition = WorkflowDefinition(
        key="ghost_agent",
        version=1,
        nodes=[NodeSpec(name="a", agent="not_registered")],
    )
    registry = WorkflowRegistry()
    with pytest.raises(Exception, match="unregistered agents"):
        registry.register(definition)
