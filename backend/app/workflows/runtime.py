"""Runtime wiring — the context factory that makes real agents run (S3.4).

The compiler builds topology; this module builds the per-node ``AgentContext``
with the live dependencies: the LLM caller (StructuredCaller over the
LLMGateway, honouring LIVE/RECORD/REPLAY and the config's model tiering), the
whitelisted tool belt, the tenant DB session, the per-run cost governor, the
DB-backed trace writer (so every agent step becomes an ``agent_executions``
row correlated to the run), storage, and research. It plugs into
``compile_workflow(context_factory=...)`` without touching topology code.

The gateway's ledger writes an ``llm_calls`` row per physical call, tagged with
the workflow execution — cost accounting is real from the first call, and
secrets never enter the ledger (only model/tokens/cost).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agents.budget import BudgetMeter
from app.agents.context import AgentContext
from app.agents.llm import CostGovernor, LLMGateway, LLMMode, RedisReplayStore
from app.agents.registry import AgentRegistry, agent_registry
from app.agents.spec import ModelPolicy
from app.agents.structured import StructuredCaller
from app.agents.tools import build_tool_belt
from app.agents.trace import DbTraceWriter
from app.core.config import Settings
from app.core.context import TenantContext
from app.core.logging import get_logger
from app.domain.enums import ExecutionStatus
from app.infrastructure.embeddings import get_default_embedder
from app.infrastructure.gemini import GeminiCallResult, GeminiClient
from app.infrastructure.storage.local import LocalFilesystemStorage
from app.infrastructure.tavily import TavilyClient
from app.workflows.compiler import ContextFactory
from app.workflows.definition import NodeSpec, WorkflowDefinition
from app.workflows.state import RunState

logger = get_logger("app.workflows.runtime")


@dataclass
class RuntimeBundle:
    """The run-level dependencies shared across every node's context."""

    session_factory: async_sessionmaker[Any]
    org_id: UUID
    execution_id: UUID
    run_id: str
    tenant: TenantContext
    settings: Settings
    redis: Any
    gateway: LLMGateway
    storage: LocalFilesystemStorage
    research: TavilyClient
    case_id: UUID | None = None
    agents: AgentRegistry | None = None
    cost_cap_usd: float = 2.0


def build_runtime_bundle(
    *,
    session_factory: async_sessionmaker[Any],
    org_id: UUID,
    execution_id: UUID,
    run_id: str,
    tenant: TenantContext,
    settings: Settings,
    redis: Any,
    case_id: UUID | None = None,
    agents: AgentRegistry | None = None,
    mode: LLMMode | None = None,
) -> RuntimeBundle:
    """Assemble the runtime bundle (gateway + ledger + storage + research)."""
    resolved_mode = mode or LLMMode(settings.agent.llm_mode)

    async def ledger(call: GeminiCallResult, purpose: str, error: str | None) -> None:
        from app.infrastructure.db.models import LLMInteraction  # local: avoid import cost

        try:
            async with session_factory() as session:
                session.add(
                    LLMInteraction(
                        org_id=org_id,
                        workflow_execution_id=execution_id,
                        model=call.model,
                        prompt_sha256=hashlib.sha256(purpose.encode()).hexdigest(),
                        tokens_in=call.tokens_in,
                        tokens_out=call.tokens_out,
                        latency_ms=call.latency_ms,
                        cost_usd=Decimal(str(call.cost_usd)),
                        status=ExecutionStatus.ERROR if error else ExecutionStatus.OK,
                        error=error,
                    )
                )
                await session.commit()
        except Exception as exc:  # the ledger must never fail a run
            logger.warning("llm_ledger_write_failed", error=str(exc))

    gateway = LLMGateway(
        GeminiClient(settings.gemini),
        redis=redis,
        mode=resolved_mode,
        replay_store=RedisReplayStore(redis) if redis is not None else None,
        ledger=ledger,
    )
    storage = LocalFilesystemStorage(settings.storage.root)
    research = TavilyClient(settings.tavily, storage)
    return RuntimeBundle(
        session_factory=session_factory,
        org_id=org_id,
        execution_id=execution_id,
        run_id=run_id,
        tenant=tenant,
        settings=settings,
        redis=redis,
        gateway=gateway,
        storage=storage,
        research=research,
        case_id=case_id,
        agents=agents or agent_registry,
        cost_cap_usd=settings.agent.run_cost_cap_usd,
    )


def make_context_factory(bundle: RuntimeBundle, definition: WorkflowDefinition) -> ContextFactory:
    """Build the per-node AgentContext factory for one run."""
    agents = bundle.agents or agent_registry

    def factory(node: NodeSpec, state: RunState) -> AgentContext:
        spec = agents.spec_for(node.agent)
        binding = definition.agents.get(node.agent)
        allowed_tools = binding.tools if binding is not None and binding.tools else spec.tools
        budget_spec = binding.budget if binding is not None and binding.budget else spec.budget

        # Model tier is authoritative from config; the spec keeps temperature.
        policy = ModelPolicy(
            primary=bundle.settings.gemini.flash_model,
            escalate_to=bundle.settings.gemini.pro_model,
            temperature=spec.model_policy.temperature,
        )
        caller = StructuredCaller(bundle.gateway, policy, spec.retry)
        governor = CostGovernor(
            cap_usd=bundle.cost_cap_usd, spent_prior=float(state.get("cost_usd", 0.0) or 0.0)
        )
        trace = DbTraceWriter(
            bundle.session_factory,
            org_id=bundle.org_id,
            workflow_execution_id=bundle.execution_id,
            run_id=bundle.run_id,
            redis=bundle.redis,
        )
        return AgentContext(
            run_id=state["run_id"],
            workflow_key=state["workflow_key"],
            node=node.name,
            state=dict(state),
            trace=trace,
            budget=BudgetMeter(budget_spec),
            caller=caller,
            tools=build_tool_belt(allowed_tools),
            tenant=bundle.tenant,
            case_id=bundle.case_id,
            session_factory=bundle.session_factory,
            governor=governor,
            storage=bundle.storage,
            research=bundle.research,
            embedder=get_default_embedder(),
            redis=bundle.redis,
            settings=bundle.settings,
        )

    return factory
