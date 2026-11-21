"""LLMGateway + StructuredCaller: cache, replay, cost breaker, repair ladder.

All offline — a fake Gemini client stands in for the SDK (no API key), a tiny
in-memory fake Redis backs the cache/limiter.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.agents.llm import (
    CostBudgetExceededError,
    CostGovernor,
    InMemoryReplayStore,
    LLMGateway,
    LLMMode,
    LLMReplayMissError,
    LLMRequest,
)
from app.agents.spec import ModelPolicy, RetryPolicy
from app.agents.structured import StructuredCaller, StructuredCallFailedError
from app.core.config import GeminiConfig
from app.infrastructure.gemini import GeminiCallResult


class Answer(BaseModel):
    verdict: str
    score: int


class FakeGeminiClient:
    """Stand-in for GeminiClient: canned response, optional N validation fails."""

    def __init__(self, *, fail_first: int = 0, fail_models: set[str] | None = None) -> None:
        self._config = GeminiConfig(flash_model="gemini-2.5-flash", pro_model="gemini-2.5-pro")
        self.configured = True
        self._fail_first = fail_first
        self._fail_models = fail_models
        self.calls: list[tuple[str, str]] = []  # (model, prompt)

    async def generate_structured(
        self,
        *,
        schema: type[BaseModel],
        prompt: str,
        image_png: bytes | None = None,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> tuple[BaseModel, GeminiCallResult]:
        chosen = model or self._config.flash_model
        self.calls.append((chosen, prompt))
        if self._fail_models is not None and chosen in self._fail_models:
            schema.model_validate({})  # raises ValidationError (missing fields)
        if len(self.calls) <= self._fail_first:
            schema.model_validate({})
        parsed = schema.model_validate({"verdict": "approve", "score": 7})
        call = GeminiCallResult(
            model=chosen, tokens_in=100, tokens_out=20, latency_ms=5, cost_usd=0.001
        )
        return parsed, call


class FakeRedis:
    """Minimal async Redis: get/set + eval (limiter always allows)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def eval(self, *args: Any) -> int:
        return 1


def _request() -> LLMRequest:
    return LLMRequest(schema_type=Answer, prompt="Assess Acme.", purpose="assess")


async def test_live_call_then_cache_hit_costs_nothing() -> None:
    client = FakeGeminiClient()
    redis = FakeRedis()
    gateway = LLMGateway(client, redis=redis, mode=LLMMode.LIVE)

    first = await gateway.complete(_request(), "gemini-2.5-flash")
    assert isinstance(first.parsed, Answer)
    assert first.cache_hit is False and first.call.cost_usd == 0.001

    second = await gateway.complete(_request(), "gemini-2.5-flash")
    assert second.cache_hit is True
    assert second.call.cost_usd == 0.0  # a cache hit is free
    assert len(client.calls) == 1  # the API was hit exactly once


async def test_record_then_replay_is_byte_identical() -> None:
    store = InMemoryReplayStore()
    recorder = LLMGateway(FakeGeminiClient(), mode=LLMMode.RECORD, replay_store=store)
    recorded = await recorder.complete(_request(), "gemini-2.5-flash")

    # A REPLAY gateway whose client would RAISE if called serves from the store.
    class ExplodingClient(FakeGeminiClient):
        async def generate_structured(self, **kwargs: Any) -> Any:
            raise AssertionError("replay mode must not call the API")

    replayer = LLMGateway(ExplodingClient(), mode=LLMMode.REPLAY, replay_store=store)
    served = await replayer.complete(_request(), "gemini-2.5-flash")
    assert served.replayed is True
    assert served.raw_text == recorded.raw_text
    assert served.parsed.model_dump() == recorded.parsed.model_dump()


async def test_replay_miss_raises() -> None:
    gateway = LLMGateway(
        FakeGeminiClient(), mode=LLMMode.REPLAY, replay_store=InMemoryReplayStore()
    )
    with pytest.raises(LLMReplayMissError):
        await gateway.complete(_request(), "gemini-2.5-flash")


def test_cost_governor_charges_trips_and_gates_escalation() -> None:
    governor = CostGovernor(cap_usd=0.50, spent_prior=0.04)
    governor.charge(0.02)
    assert round(governor.total(), 4) == 0.06
    assert governor.escalation_allowed() is True  # remaining 0.44 >= 0.05 reserve

    tight = CostGovernor(cap_usd=0.10, spent_prior=0.04)
    tight.charge(0.02)  # remaining 0.04 < 0.05 reserve
    assert tight.escalation_allowed() is False

    with pytest.raises(CostBudgetExceededError):
        tight.charge(0.10)


async def test_governor_charges_only_on_live_calls() -> None:
    redis = FakeRedis()
    client = FakeGeminiClient()
    gateway = LLMGateway(client, redis=redis, mode=LLMMode.LIVE)
    governor = CostGovernor(cap_usd=1.0)

    await gateway.complete(_request(), "gemini-2.5-flash", governor=governor)
    assert round(governor.node_spent, 4) == 0.001
    # Cache hit charges nothing more.
    await gateway.complete(_request(), "gemini-2.5-flash", governor=governor)
    assert round(governor.node_spent, 4) == 0.001


async def test_structured_caller_repairs_then_succeeds() -> None:
    client = FakeGeminiClient(fail_first=2)
    gateway = LLMGateway(client, mode=LLMMode.LIVE)  # no redis: no caching of failures
    caller = StructuredCaller(
        gateway,
        ModelPolicy(primary="gemini-2.5-flash", escalate_to="gemini-2.5-pro"),
        RetryPolicy(repair_max=2, regen_max=1),
    )
    result = await caller.call(_request())
    assert isinstance(result.parsed, Answer)
    assert len(client.calls) == 3  # 2 failures + 1 success, all on flash
    assert all(model == "gemini-2.5-flash" for model, _ in client.calls)
    # The repair attempts appended the validation error to the prompt.
    assert "failed schema validation" in client.calls[1][1]


async def test_structured_caller_escalates_to_pro() -> None:
    client = FakeGeminiClient(fail_models={"gemini-2.5-flash"})
    gateway = LLMGateway(client, mode=LLMMode.LIVE)
    caller = StructuredCaller(
        gateway,
        ModelPolicy(primary="gemini-2.5-flash", escalate_to="gemini-2.5-pro"),
        RetryPolicy(repair_max=1, regen_max=1),
    )
    result = await caller.call(_request())
    assert result.call.model == "gemini-2.5-pro"
    assert any(model == "gemini-2.5-pro" for model, _ in client.calls)


async def test_structured_caller_gives_up_typed() -> None:
    client = FakeGeminiClient(fail_models={"gemini-2.5-flash", "gemini-2.5-pro"})
    gateway = LLMGateway(client, mode=LLMMode.LIVE)
    caller = StructuredCaller(
        gateway,
        ModelPolicy(primary="gemini-2.5-flash", escalate_to="gemini-2.5-pro"),
        RetryPolicy(repair_max=1, regen_max=1),
    )
    with pytest.raises(StructuredCallFailedError):
        await caller.call(_request())


async def test_exhausted_governor_blocks_escalation() -> None:
    client = FakeGeminiClient(fail_models={"gemini-2.5-flash"})
    gateway = LLMGateway(client, mode=LLMMode.LIVE)
    caller = StructuredCaller(
        gateway,
        ModelPolicy(primary="gemini-2.5-flash", escalate_to="gemini-2.5-pro"),
        RetryPolicy(repair_max=0, regen_max=1),
    )
    governor = CostGovernor(cap_usd=0.01, spent_prior=0.01)  # nothing left to escalate
    with pytest.raises(StructuredCallFailedError):
        await caller.call(_request(), governor=governor)
    assert all(model == "gemini-2.5-flash" for model, _ in client.calls)  # never reached pro


async def test_ledger_records_each_physical_call() -> None:
    recorded: list[tuple[str, str, str | None]] = []

    async def ledger(call: GeminiCallResult, purpose: str, error: str | None) -> None:
        recorded.append((call.model, purpose, error))

    gateway = LLMGateway(FakeGeminiClient(), mode=LLMMode.LIVE, ledger=ledger)
    await gateway.complete(_request(), "gemini-2.5-flash")
    assert recorded == [("gemini-2.5-flash", "assess", None)]


class _TransientClient(FakeGeminiClient):
    """Raises a transient (503-like) error for the first N calls, then succeeds."""

    def __init__(self, *, transient_fails: int = 0, always: bool = False) -> None:
        super().__init__()
        self._transient_fails = transient_fails
        self._always = always

    async def generate_structured(self, **kwargs: Any) -> Any:
        model = kwargs.get("model") or self._config.flash_model
        self.calls.append((model, kwargs.get("prompt", "")))
        if self._always or len(self.calls) <= self._transient_fails:
            raise RuntimeError("503 UNAVAILABLE: the model is experiencing high demand")
        parsed = kwargs["schema"].model_validate({"verdict": "approve", "score": 7})
        return parsed, GeminiCallResult(model=model, cost_usd=0.001)


async def test_gateway_retries_transient_then_succeeds() -> None:
    client = _TransientClient(transient_fails=2)
    gateway = LLMGateway(
        client, mode=LLMMode.LIVE, max_transient_retries=3, transient_backoff_base=0.0
    )
    result = await gateway.complete(_request(), "gemini-2.5-flash")
    assert isinstance(result.parsed, Answer)
    assert len(client.calls) == 3  # 2 transient failures + 1 success


async def test_gateway_raises_after_transient_retries_exhausted() -> None:
    client = _TransientClient(always=True)
    gateway = LLMGateway(
        client, mode=LLMMode.LIVE, max_transient_retries=2, transient_backoff_base=0.0
    )
    with pytest.raises(RuntimeError):
        await gateway.complete(_request(), "gemini-2.5-flash")
    assert len(client.calls) == 3  # initial + 2 retries


async def test_gateway_transient_falls_back_to_replay() -> None:
    store = InMemoryReplayStore()
    recorder = LLMGateway(FakeGeminiClient(), mode=LLMMode.RECORD, replay_store=store)
    recorded = await recorder.complete(_request(), "gemini-2.5-flash")

    client = _TransientClient(always=True)
    gateway = LLMGateway(
        client,
        mode=LLMMode.LIVE,
        replay_store=store,
        max_transient_retries=1,
        transient_backoff_base=0.0,
    )
    served = await gateway.complete(_request(), "gemini-2.5-flash")
    assert served.raw_text == recorded.raw_text  # explicit replay fallback
    assert len(client.calls) == 2  # initial + 1 retry, then fell back
