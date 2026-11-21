"""Thin Gemini client for Phase-4 pipeline stages (vision OCR escalation,
classification tier-2, LLM-backed extraction).

Deliberately minimal: the full LLMGateway (Redis prompt-hash cache, global
token bucket, cost circuit breaker, record/replay) is a Phase-5 deliverable
(ADR-0005) and will wrap or replace this client behind the same call shape.
What this client already guarantees:

- **optional key**: constructing without ``IC_GEMINI__API_KEY`` is fine;
  calling raises :class:`GeminiUnconfiguredError` — pipeline stages park with
  a machine-readable error instead of faking output;
- **structured output**: every call binds a Pydantic schema via the
  google-genai native ``response_schema`` and re-validates client-side;
- **ledger**: every call (success or error) is recorded in the
  ``llm_interactions`` table by the caller-supplied recorder — cost
  accounting exists from the first real call (ADR-0012).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

from pydantic import BaseModel

from app.core.config import GeminiConfig
from app.core.errors import AppError
from app.core.logging import get_logger
from app.core.metrics import observe_llm_call

logger = get_logger("app.infrastructure.gemini")

ModelT = TypeVar("ModelT", bound=BaseModel)

# USD per 1M tokens (input, output) for the cost ledger; thinking tokens bill
# as output. The 3.x rates are estimates pending a pricing-page confirmation —
# the cost governor/circuit-breaker mechanism is exact regardless of the rate;
# an unlisted model simply logs a zero-cost estimate (see estimate_cost_usd).
_PRICING: dict[str, tuple[float, float]] = {
    "gemini-3.7-flash": (0.30, 2.50),
    "gemini-3.6-flash": (0.30, 2.50),
    "gemini-3.5-flash": (0.30, 2.50),
    "gemini-3.1-pro-preview": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),  # retained for legacy replay fixtures
    "gemini-2.5-pro": (1.25, 10.00),
}


class GeminiUnconfiguredError(AppError):
    """Raised when an LLM-dependent stage runs without an API key."""

    code = "gemini_unconfigured"
    status_code = 503


class GeminiCallResult(BaseModel):
    """Metadata of one completed Gemini call (for the llm_interactions row)."""

    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0


LedgerRecorder = Callable[[GeminiCallResult, str | None], Awaitable[None]]
"""Callback persisting a call record; second arg is the error string if any."""


def estimate_cost_usd(model: str, tokens_in: int, tokens_out: int) -> float:
    """Ledger cost estimate from the pricing table (0.0 for unknown models)."""
    price_in, price_out = _PRICING.get(model, (0.0, 0.0))
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


class GeminiClient:
    """Schema-validated Gemini calls with optional-key semantics."""

    def __init__(self, config: GeminiConfig) -> None:
        self._config = config
        self._client: object | None = None

    @property
    def configured(self) -> bool:
        """Whether an API key is present (stages branch on this honestly)."""
        return bool(self._config.api_key)

    def _ensure_client(self) -> object:
        if not self._config.api_key:
            raise GeminiUnconfiguredError(
                "IC_GEMINI__API_KEY is not set; LLM-dependent stages cannot run"
            )
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._config.api_key)
        return self._client

    async def generate_structured(
        self,
        *,
        schema: type[ModelT],
        prompt: str,
        image_png: bytes | None = None,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> tuple[ModelT, GeminiCallResult]:
        """One structured-output call (optionally multimodal with a PNG page).

        Raises :class:`GeminiUnconfiguredError` without a key; propagates API
        errors after recording latency. Validation errors surface as-is —
        the Phase-5 gateway adds the repair loop (ADR-0005).
        """
        client = self._ensure_client()
        from google.genai import types as genai_types

        chosen_model = model or self._config.flash_model
        parts: list[object] = [prompt]
        if image_png is not None:
            parts.append(genai_types.Part.from_bytes(data=image_png, mime_type="image/png"))

        started = time.perf_counter()
        response = await client.aio.models.generate_content(  # type: ignore[attr-defined]
            model=chosen_model,
            contents=parts,
            config=genai_types.GenerateContentConfig(
                temperature=temperature,
                response_mime_type="application/json",
                response_schema=schema,
            ),
        )
        latency_ms = int((time.perf_counter() - started) * 1000)

        usage = getattr(response, "usage_metadata", None)
        tokens_in = int(getattr(usage, "prompt_token_count", 0) or 0)
        tokens_out = int(getattr(usage, "candidates_token_count", 0) or 0) + int(
            getattr(usage, "thoughts_token_count", 0) or 0
        )
        result = GeminiCallResult(
            model=chosen_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
            cost_usd=estimate_cost_usd(chosen_model, tokens_in, tokens_out),
        )

        parsed = getattr(response, "parsed", None)
        validated = (
            parsed if isinstance(parsed, schema) else schema.model_validate_json(response.text)
        )
        logger.info(
            "gemini_call",
            model=chosen_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=latency_ms,
        )
        # A physical (live) API hit: cache/replay never reach here, so this is
        # the single non-double-counting emit point for live LLM latency/cost.
        observe_llm_call(
            chosen_model,
            status="ok",
            mode="live",
            duration_s=latency_ms / 1000.0,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=result.cost_usd,
        )
        return validated, result
