"""AgentContext — the only way an agent touches the world (ARCHITECTURE §5).

Injected per node execution, which keeps agents unit-testable and makes every
capability a swappable port. Phase 5 extends the context with the LLM gateway,
tool belt, and memory tiers; Phase 5 also replaces the logging trace writer
with the Postgres-backed evidence-ledger writer (ADR-0012) — same protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol
from uuid import UUID

from pydantic import BaseModel, Field

from app.agents.budget import BudgetMeter
from app.core.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.agents.llm import CostGovernor
    from app.agents.structured import StructuredCallerPort
    from app.agents.tools import ToolBelt
    from app.application.ports import EmbeddingProviderPort, StoragePort
    from app.core.context import TenantContext
    from app.infrastructure.tavily import TavilyClient


class TraceEvent(BaseModel):
    """One lifecycle/tool/LLM event emitted by an agent step."""

    node: str
    agent: str
    seq: int
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TraceWriterPort(Protocol):
    """Sink for trace events (structlog now; AgentExecution rows in Phase 5)."""

    async def record(self, event: TraceEvent) -> None:
        """Persist or emit one trace event."""
        ...


class LoggingTraceWriter:
    """Phase-2 trace writer: structured log lines only."""

    def __init__(self) -> None:
        self._logger = get_logger("app.agents.trace")

    async def record(self, event: TraceEvent) -> None:
        """Emit the event as one structured log line."""
        self._logger.info(
            "agent_trace",
            node=event.node,
            agent=event.agent,
            seq=event.seq,
            kind=event.kind,
            payload=event.payload,
            ts=event.ts.isoformat(),
        )


class ComponentLedger:
    """Collects measured confidence components during a node's lifecycle.

    The Phase-2 review's requested seam: agents record honest components
    (schema, critic, validators, evidence, source, tool) as they run; the
    BaseAgent lifecycle folds them into the weighted-geometric-mean score
    (ARCHITECTURE §5).
    """

    def __init__(self) -> None:
        self._components: dict[str, float] = {}

    def set(self, name: str, value: float) -> None:
        self._components[name] = value

    def merge_missing(self, other: dict[str, float]) -> None:
        for key, value in other.items():
            self._components.setdefault(key, value)

    def values(self) -> dict[str, float]:
        return dict(self._components)


@dataclass
class AgentContext:
    """Everything an agent may use during one node execution.

    Phase-2 fields are required; the Phase-5 capabilities (LLM caller, tools,
    tenant/case, DB, cost governor, storage, research) are injected by the
    workflow compiler and default to None so the runtime stays unit-testable
    and the no-op agent needs nothing.
    """

    run_id: str
    workflow_key: str
    node: str
    state: dict[str, Any]
    trace: TraceWriterPort
    budget: BudgetMeter
    logger: Any = field(default_factory=lambda: get_logger("app.agents"))
    components: ComponentLedger = field(default_factory=ComponentLedger)
    caller: StructuredCallerPort | None = None
    tools: ToolBelt | None = None
    tenant: TenantContext | None = None
    case_id: UUID | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None
    governor: CostGovernor | None = None
    storage: StoragePort | None = None
    research: TavilyClient | None = None
    embedder: EmbeddingProviderPort | None = None
    redis: Any = None
    settings: Any = None
