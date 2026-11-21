"""BaseAgent — one lifecycle for all twelve platform agents (ARCHITECTURE §5).

The lifecycle is final: ``plan -> act -> reflect -> (revise once) -> score``.
Subclasses override the hooks, never the loop, so planning, bounded
reflection, tracing, budgets, and confidence are uniform by construction.
Phase 5 supplies the real hooks (Gemini structured calls, deterministic
validators, the rubric critic); Phase 2 ships the runtime with honest
defaults — no fake AI.
"""

from __future__ import annotations

import statistics
import time
from abc import ABC, abstractmethod
from typing import final

from pydantic import BaseModel, Field

from app.agents.context import AgentContext, TraceEvent
from app.agents.results import AgentResult, ConfidenceReport, Critique, compute_confidence
from app.agents.spec import AgentSpec
from app.core.metrics import observe_agent_node


class PlannedStep(BaseModel):
    """One intended action in an agent's plan."""

    action: str
    rationale: str = ""


class Plan(BaseModel):
    """Typed plan; for most agents the workflow graph IS the plan."""

    steps: list[PlannedStep] = Field(default_factory=list)


class BaseAgent(ABC):
    """Abstract agent with a final run() lifecycle and overridable hooks."""

    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec

    @final
    async def run(self, ctx: AgentContext) -> AgentResult:
        """Execute the full lifecycle and return a confidence-scored result."""
        seq = 0
        _t0 = time.perf_counter()

        plan = await self.plan(ctx)
        await ctx.trace.record(
            TraceEvent(
                node=ctx.node,
                agent=self.spec.name,
                seq=seq,
                kind="plan",
                payload={"steps": [step.model_dump() for step in plan.steps]},
            )
        )

        seq += 1
        draft = await self.act(ctx, plan)
        await ctx.trace.record(TraceEvent(node=ctx.node, agent=self.spec.name, seq=seq, kind="act"))

        seq += 1
        critique = await self.reflect(ctx, draft)
        await ctx.trace.record(
            TraceEvent(
                node=ctx.node,
                agent=self.spec.name,
                seq=seq,
                kind="reflect",
                payload={"passed": critique.passed, "issues": critique.issues},
            )
        )

        if not critique.passed and self.spec.reflection.max_revisions > 0:
            seq += 1
            draft = await self.revise(ctx, draft, critique)
            critique = await self.reflect(ctx, draft)
            await ctx.trace.record(
                TraceEvent(
                    node=ctx.node,
                    agent=self.spec.name,
                    seq=seq,
                    kind="revise",
                    payload={"passed": critique.passed},
                )
            )

        confidence = self._score(ctx, critique)
        seq += 1
        await ctx.trace.record(
            TraceEvent(
                node=ctx.node,
                agent=self.spec.name,
                seq=seq,
                kind="final",
                payload={
                    "confidence": confidence.score,
                    "budget": ctx.budget.snapshot(),
                },
            )
        )
        observe_agent_node(self.spec.name, "ok", time.perf_counter() - _t0)
        return AgentResult(output=draft, confidence=confidence)

    async def plan(self, ctx: AgentContext) -> Plan:
        """Default single-step static plan; Research/News override in Phase 5."""
        return Plan(steps=[PlannedStep(action=f"execute:{self.spec.name}")])

    @abstractmethod
    async def act(self, ctx: AgentContext, plan: Plan) -> BaseModel:
        """Produce the agent's draft output (tool loop + synthesis in Phase 5)."""

    async def reflect(self, ctx: AgentContext, draft: BaseModel) -> Critique:
        """Default critique passes; validators + rubric critic land in Phase 5."""
        return Critique(
            passed=True,
            rubric_scores={"groundedness": 1.0, "completeness": 1.0, "consistency": 1.0},
        )

    async def revise(self, ctx: AgentContext, draft: BaseModel, critique: Critique) -> BaseModel:
        """Default revision returns the draft unchanged (bounded Reflexion seam)."""
        return draft

    def _score(self, ctx: AgentContext, critique: Critique) -> ConfidenceReport:
        """Fold agent-recorded components and the critique into the wgm score.

        Components set by the agent during its lifecycle (schema, evidence,
        source, tool — via ``ctx.components``) take precedence; the critic and
        validator components derive from the reflection critique when the agent
        did not set them itself.
        """
        components = ctx.components.values()
        if "critic" not in components and critique.rubric_scores:
            components["critic"] = statistics.fmean(critique.rubric_scores.values())
        if "validators" not in components and critique.validator_pass_ratio is not None:
            components["validators"] = critique.validator_pass_ratio
        return compute_confidence(self.spec.confidence_weights, components)
