"""StructuredCaller — the repair + model-escalation ladder (ADR-0005).

Sits above the :class:`~app.agents.llm.LLMGateway`: one physical call per
attempt, driving the retry ladder the ADR specifies —

1. call on the primary tier (Flash);
2. on a schema-validation failure, re-prompt with the validation error
   appended (``repair_max`` attempts) — native ``response_schema`` makes this
   rare, but malformed output must never crash a run;
3. still failing, escalate to the higher tier (Pro) for ``regen_max``
   attempts — unless the per-run cost governor has disabled escalation, in
   which case the run stays on Flash and the failure surfaces typed.

The caller returns a validated :class:`~app.agents.llm.LLMResult`; the agent
lifecycle turns a terminal failure into a degraded-node parking + HITL flag
(ARCHITECTURE §5), never a dead run.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import ValidationError

from app.agents.llm import CostGovernor, LLMGateway, LLMRequest, LLMResult
from app.agents.spec import ModelPolicy, RetryPolicy
from app.core.errors import AppError
from app.core.logging import get_logger

logger = get_logger("app.agents.structured")


class StructuredCallerPort(Protocol):
    """What agents depend on for structured LLM calls (real caller or a test
    double). Keeps :class:`GenericLLMAgent` unit-testable without any key."""

    async def call(
        self, request: LLMRequest, *, governor: CostGovernor | None = None
    ) -> LLMResult: ...


class StructuredCallFailedError(AppError):
    """The repair + escalation ladder exhausted without a valid response."""

    code = "structured_call_failed"
    status_code = 502


class StructuredCaller:
    """Drives repair + escalation over an :class:`LLMGateway`."""

    def __init__(self, gateway: LLMGateway, model_policy: ModelPolicy, retry: RetryPolicy) -> None:
        self._gateway = gateway
        self._policy = model_policy
        self._retry = retry

    async def call(self, request: LLMRequest, *, governor: CostGovernor | None = None) -> LLMResult:
        """Return a validated result or raise :class:`StructuredCallFailedError`."""
        last_error: Exception | None = None

        # Tier 1: primary model with a bounded repair loop.
        for attempt in range(self._retry.repair_max + 1):
            attempt_request = request if attempt == 0 else self._repair(request, last_error)
            try:
                return await self._gateway.complete(
                    attempt_request, self._policy.primary, governor=governor
                )
            except ValidationError as exc:
                last_error = exc
                logger.info(
                    "structured_repair",
                    purpose=request.purpose,
                    model=self._policy.primary,
                    attempt=attempt,
                )

        # Tier 2: escalate to the higher model, if configured and affordable.
        escalate_to = self._policy.escalate_to
        may_escalate = (
            escalate_to is not None
            and escalate_to != self._policy.primary
            and (governor is None or governor.escalation_allowed())
        )
        if may_escalate:
            assert escalate_to is not None
            for attempt in range(self._retry.regen_max + 1):
                attempt_request = request if attempt == 0 else self._repair(request, last_error)
                try:
                    return await self._gateway.complete(
                        attempt_request, escalate_to, governor=governor
                    )
                except ValidationError as exc:
                    last_error = exc
                    logger.info(
                        "structured_escalated_repair",
                        purpose=request.purpose,
                        model=escalate_to,
                        attempt=attempt,
                    )

        raise StructuredCallFailedError(
            f"Structured call {request.purpose!r} failed schema validation after repair",
            details={"error": str(last_error)[:500] if last_error else None},
        )

    @staticmethod
    def _repair(request: LLMRequest, error: Exception | None) -> LLMRequest:
        """Re-prompt with the validation error appended for correction."""
        suffix = (
            "\n\nYour previous response failed schema validation with the following "
            f"errors. Return ONLY valid JSON matching the schema.\nErrors:\n{error}"
        )
        return request.model_copy(update={"prompt": request.prompt + suffix})
