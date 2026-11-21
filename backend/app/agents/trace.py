"""DbTraceWriter — the evidence ledger IS the observability trace (ADR-0012).

Every agent lifecycle/LLM/tool event becomes one ``agent_executions`` row and
a compact Redis event for the live run SSE. The same rows power the
judge-facing "Explain" replay and operator debugging — one write path. LLM
cost/token detail also flows to ``llm_calls`` via the gateway ledger; here we
capture the node-by-node lifecycle with confidence and citations.

Payload redaction happens at write time (never in the UI): known
secret-bearing keys are dropped before the row is stored (§15.6).
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.agents.context import TraceEvent
from app.core.logging import get_logger
from app.domain.enums import AgentExecutionKind, ExecutionStatus
from app.infrastructure.db.models import AgentExecution

logger = get_logger("app.agents.trace")

_KIND_MAP = {
    "plan": AgentExecutionKind.PLAN,
    "act": AgentExecutionKind.LLM_CALL,
    "llm_call": AgentExecutionKind.LLM_CALL,
    "tool_call": AgentExecutionKind.TOOL_CALL,
    "reflect": AgentExecutionKind.REFLECT,
    "revise": AgentExecutionKind.REVISE,
    "final": AgentExecutionKind.FINAL,
    "error": AgentExecutionKind.ERROR,
}

_REDACT_KEYS = frozenset({"prompt", "api_key", "password", "token"})


def run_channel(run_id: str) -> str:
    return f"run:{run_id}:events"


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if k not in _REDACT_KEYS}


class DbTraceWriter:
    """Persists trace events as agent_executions rows + publishes SSE deltas."""

    def __init__(
        self,
        session_factory: Any,
        *,
        org_id: UUID,
        workflow_execution_id: UUID,
        run_id: str,
        redis: Any = None,
    ) -> None:
        self._session_factory = session_factory
        self._org_id = org_id
        self._run_id = workflow_execution_id
        self._run_key = run_id
        self._redis = redis

    async def record(self, event: TraceEvent) -> None:
        payload = _redact(event.payload)
        status = (
            ExecutionStatus.ERROR
            if event.kind == "error" or payload.get("error")
            else ExecutionStatus.OK
        )
        try:
            async with self._session_factory() as session:
                session.add(
                    AgentExecution(
                        org_id=self._org_id,
                        workflow_execution_id=self._run_id,
                        node=event.node,
                        agent=event.agent or event.node,
                        seq=event.seq,
                        kind=_KIND_MAP.get(event.kind, AgentExecutionKind.LLM_CALL),
                        status=status,
                        model=payload.get("model"),
                        output=payload,
                        error=payload.get("error"),
                        tokens_in=int(payload.get("tokens_in", 0) or 0),
                        tokens_out=int(payload.get("tokens_out", 0) or 0),
                        latency_ms=int(payload.get("latency_ms", 0) or 0),
                        cost_usd=Decimal(str(payload.get("cost_usd", 0) or 0)),
                        confidence=payload.get("confidence"),
                        citations=payload.get("citations"),
                    )
                )
                await session.commit()
        except Exception as exc:  # tracing must never crash a run
            logger.warning("trace_write_failed", node=event.node, error=str(exc))

        await self._publish(event)

    async def _publish(self, event: TraceEvent) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.publish(
                run_channel(self._run_key),
                json.dumps(
                    {
                        "type": "agent_step",
                        "node": event.node,
                        "agent": event.agent,
                        "seq": event.seq,
                        "kind": event.kind,
                        "payload": _redact(event.payload),
                        "ts": event.ts.isoformat(),
                    }
                ),
            )
        except Exception as exc:
            logger.warning("trace_publish_failed", error=str(exc))
