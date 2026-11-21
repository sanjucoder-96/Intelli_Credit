"""Agent execution trace and LLM interaction ledger.

``AgentExecution`` is the ARCHITECTURE 'AgentStep' — the first-party
evidence-ledger trace (ADR-0012). ``LLMInteraction`` is the 'llm_calls'
cost/cache ledger (ADR-0005). Phase 5's TraceWriter persists rows here;
until then the tables exist and the LoggingTraceWriter emits to structlog.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.enums import AgentExecutionKind, ExecutionStatus
from app.infrastructure.db.base import (
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    string_enum,
)


class AgentExecution(UUIDPrimaryKeyMixin, TenantMixin, TimestampMixin, Base):
    """One lifecycle/LLM/tool event of an agent run (ADR-0012)."""

    __tablename__ = "agent_executions"
    __table_args__ = (
        sa.Index("ix_agent_executions_workflow_execution_id_seq", "workflow_execution_id", "seq"),
    )

    workflow_execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflow_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    node: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    agent: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    seq: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    kind: Mapped[AgentExecutionKind] = mapped_column(
        string_enum(AgentExecutionKind), nullable=False
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        string_enum(ExecutionStatus), nullable=False, default=ExecutionStatus.OK
    )
    model: Mapped[str | None] = mapped_column(sa.String(64), default=None)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    error: Mapped[str | None] = mapped_column(sa.Text, default=None)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(sa.Numeric(12, 6), nullable=False, default=Decimal(0))
    confidence: Mapped[float | None] = mapped_column(sa.Float, default=None)
    confidence_components: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    citations: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)


class LLMInteraction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """One LLM call in the 'llm_calls' ledger (ADR-0005).

    ``org_id`` is nullable directly (not via TenantMixin): platform-level
    calls (e.g. Phase 5 gateway health probes) have no tenant.
    """

    __tablename__ = "llm_interactions"
    __table_args__ = (sa.Index("ix_llm_interactions_model_created_at", "model", "created_at"),)

    org_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
        default=None,
        index=True,
    )
    workflow_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("workflow_executions.id", ondelete="SET NULL"),
        default=None,
    )
    agent_execution_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("agent_executions.id", ondelete="SET NULL"),
        default=None,
    )
    model: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    prompt_sha256: Mapped[str] = mapped_column(sa.String(64), nullable=False, index=True)
    cached: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(sa.Numeric(12, 6), nullable=False, default=Decimal(0))
    status: Mapped[ExecutionStatus] = mapped_column(
        string_enum(ExecutionStatus), nullable=False, default=ExecutionStatus.OK
    )
    error: Mapped[str | None] = mapped_column(sa.Text, default=None)
    payload_ref: Mapped[str | None] = mapped_column(sa.String(512), default=None)


class AgentExecutionPayload(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Gzipped prompt/output payloads for one agent execution (ADR-0012).

    A side table so the hot ``agent_executions`` rows stay narrow while full
    prompt replay remains available to the trace UI. Payloads are redacted by
    the TraceWriter BEFORE compression (secrets never reach this table).
    Tenant scope is inherited through the agent execution.
    """

    __tablename__ = "agent_execution_payloads"

    agent_execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        sa.ForeignKey("agent_executions.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    prompt_gz: Mapped[bytes | None] = mapped_column(sa.LargeBinary, default=None)
    output_gz: Mapped[bytes | None] = mapped_column(sa.LargeBinary, default=None)
