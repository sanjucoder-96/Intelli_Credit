"""BaseAgent lifecycle, registry behaviour, and budget enforcement."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.agents.base import BaseAgent, Plan
from app.agents.budget import BudgetExceededError, BudgetMeter
from app.agents.context import AgentContext, LoggingTraceWriter, TraceEvent
from app.agents.registry import AgentRegistry, NoopOutput, agent_registry
from app.agents.results import Critique
from app.agents.spec import AgentSpec, StepBudget
from app.core.errors import NotFoundError


class RecordingTraceWriter:
    """Captures trace events for assertions."""

    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    async def record(self, event: TraceEvent) -> None:
        self.events.append(event)


def make_ctx(
    trace: RecordingTraceWriter | None = None, budget: StepBudget | None = None
) -> AgentContext:
    return AgentContext(
        run_id="run-x",
        workflow_key="wf-x",
        node="node-x",
        state={},
        trace=trace or LoggingTraceWriter(),
        budget=BudgetMeter(budget or StepBudget()),
    )


async def test_noop_agent_is_registered_and_runs() -> None:
    assert "noop" in agent_registry.names()
    agent = agent_registry.create("noop")
    result = await agent.run(make_ctx())
    assert result.output == NoopOutput()
    assert 0.0 <= result.confidence.score <= 1.0
    assert result.confidence.method == "wgm-v1"


async def test_unknown_agent_raises_not_found() -> None:
    with pytest.raises(NotFoundError, match="not registered"):
        agent_registry.create("does_not_exist")


async def test_lifecycle_order_and_trace_kinds() -> None:
    trace = RecordingTraceWriter()
    agent = agent_registry.create("noop")
    await agent.run(make_ctx(trace=trace))

    kinds = [event.kind for event in trace.events]
    assert kinds == ["plan", "act", "reflect", "final"]
    assert all(event.node == "node-x" for event in trace.events)
    assert trace.events[-1].payload["budget"]["llm_calls"] == 0.0


async def test_failed_reflection_triggers_single_revision() -> None:
    calls: list[str] = []

    class SelfCriticalAgent(BaseAgent):
        async def act(self, ctx: AgentContext, plan: Plan) -> BaseModel:
            calls.append("act")
            return NoopOutput()

        async def reflect(self, ctx: AgentContext, draft: BaseModel) -> Critique:
            calls.append("reflect")
            passed = calls.count("reflect") > 1
            return Critique(passed=passed, rubric_scores={"groundedness": 0.5})

        async def revise(
            self, ctx: AgentContext, draft: BaseModel, critique: Critique
        ) -> BaseModel:
            calls.append("revise")
            return draft

    agent = SelfCriticalAgent(AgentSpec(name="self_critical"))
    await agent.run(make_ctx())
    assert calls == ["act", "reflect", "revise", "reflect"]


async def test_budget_meter_enforces_llm_cap() -> None:
    meter = BudgetMeter(StepBudget(max_llm_calls=1, max_tool_calls=1, max_cost_usd=1.0))
    meter.charge_llm()
    with pytest.raises(BudgetExceededError):
        meter.charge_llm()


async def test_budget_meter_enforces_cost_cap() -> None:
    meter = BudgetMeter(StepBudget(max_llm_calls=10, max_tool_calls=10, max_cost_usd=0.10))
    meter.charge_llm(cost_usd=0.08)
    with pytest.raises(BudgetExceededError) as excinfo:
        meter.charge_llm(cost_usd=0.05)
    assert excinfo.value.code == "budget_exceeded"
    assert excinfo.value.status_code == 409


async def test_registry_spec_override() -> None:
    registry = AgentRegistry()

    class EchoAgent(BaseAgent):
        async def act(self, ctx: AgentContext, plan: Plan) -> BaseModel:
            return NoopOutput(note=self.spec.name)

    registry.register("echo")(EchoAgent)
    custom = registry.create("echo", AgentSpec(name="custom_echo"))
    result = await custom.run(make_ctx())
    assert result.output == NoopOutput(note="custom_echo")
