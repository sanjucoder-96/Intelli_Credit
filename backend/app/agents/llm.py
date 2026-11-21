"""LLMGateway — the single seam for every model call (ADR-0005).

One place owns cache, rate limiting, the per-run cost circuit breaker, the
cost ledger, and record/replay. Agents never touch the Gemini SDK directly;
they go through :class:`~app.agents.structured.StructuredCaller`, which drives
this gateway's repair/escalation ladder.

Modes:

- ``LIVE``   — call the API; cache successful responses in Redis.
- ``RECORD`` — LIVE, plus persist every response to the durable replay store
  so a later run can be reproduced byte-identically.
- ``REPLAY`` — serve only from the replay store; never call the API. This is
  the CI seam (tests run with no key) and the demo-day offline fallback: a
  recorded run streams back through the real graph identically.

The cost circuit breaker (:class:`CostGovernor`) is a hard per-run USD ceiling
carried in graph state — not a ledger. When it is exhausted the caller stops
escalating to Pro and the run completes on Flash with warnings; a runaway
loop can never become an unbounded bill.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.errors import AppError
from app.core.logging import get_logger
from app.infrastructure.gemini import (
    GeminiCallResult,
    GeminiClient,
    GeminiUnconfiguredError,
)

logger = get_logger("app.agents.llm")

ModelT = TypeVar("ModelT", bound=BaseModel)

_CACHE_PREFIX = "llmcache:"
_REPLAY_PREFIX = "llmreplay:"
_CACHE_TTL_SECONDS = 3600


class LLMMode(StrEnum):
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"


class LLMRequest(BaseModel):
    """One structured-output request (the cache/replay key is derived from it)."""

    model_config = {"arbitrary_types_allowed": True}

    schema_type: type[BaseModel]
    prompt: str
    purpose: str = "generate"
    image_png: bytes | None = None
    temperature: float = 0.1

    def fingerprint(self, model: str) -> str:
        """Deterministic hash over the physical-call inputs."""
        hasher = hashlib.sha256()
        hasher.update(model.encode())
        hasher.update(b"\x00")
        hasher.update(f"{self.schema_type.__module__}.{self.schema_type.__qualname__}".encode())
        hasher.update(b"\x00")
        hasher.update(self.prompt.encode())
        if self.image_png is not None:
            hasher.update(b"\x00img\x00")
            hasher.update(hashlib.sha256(self.image_png).digest())
        return hasher.hexdigest()


class LLMResult(BaseModel):
    """A validated structured response plus provenance."""

    model_config = {"arbitrary_types_allowed": True}

    parsed: BaseModel
    raw_text: str
    call: GeminiCallResult
    cache_hit: bool = False
    replayed: bool = False


class CostBudgetExceededError(AppError):
    """The per-run USD ceiling was reached (circuit breaker tripped)."""

    code = "cost_budget_exceeded"
    status_code = 409


class CostGovernor:
    """Per-run USD circuit breaker, reconstructed from graph state per node.

    ``spent_prior`` is the run's total at node entry; this governor tracks
    only *this* node's spend so parallel branches can each return their delta
    for summation into state. ``escalation_allowed`` gates Flash→Pro once the
    remaining budget falls below a Pro-call reserve.
    """

    PRO_CALL_RESERVE_USD = 0.05

    def __init__(self, cap_usd: float, spent_prior: float = 0.0) -> None:
        self._cap = cap_usd
        self._spent_prior = spent_prior
        self._node_spent = 0.0

    @property
    def node_spent(self) -> float:
        return self._node_spent

    def total(self) -> float:
        return self._spent_prior + self._node_spent

    def remaining(self) -> float:
        return max(0.0, self._cap - self.total())

    def escalation_allowed(self) -> bool:
        return self.remaining() >= self.PRO_CALL_RESERVE_USD

    def would_exceed(self, cost: float) -> bool:
        return self.total() + cost > self._cap

    def charge(self, cost: float) -> None:
        if self.would_exceed(cost):
            raise CostBudgetExceededError(
                "Per-run LLM cost ceiling reached",
                details={"cap_usd": self._cap, "total_usd": round(self.total(), 6)},
            )
        self._node_spent += cost


class ReplayStore(Protocol):
    """Durable request→response store for RECORD/REPLAY."""

    async def get(self, key: str) -> tuple[str, dict[str, Any]] | None: ...

    async def put(self, key: str, raw_text: str, call: dict[str, Any]) -> None: ...


class InMemoryReplayStore:
    """Process-local replay store (tests, single-process demos)."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[str, dict[str, Any]]] = {}

    async def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        return self._data.get(key)

    async def put(self, key: str, raw_text: str, call: dict[str, Any]) -> None:
        self._data[key] = (raw_text, call)


class RedisReplayStore:
    """Redis-backed replay store (no TTL — recorded fixtures persist)."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        import json

        raw = await self._redis.get(_REPLAY_PREFIX + key)
        if raw is None:
            return None
        payload = json.loads(raw)
        return payload["raw_text"], payload["call"]

    async def put(self, key: str, raw_text: str, call: dict[str, Any]) -> None:
        import json

        await self._redis.set(
            _REPLAY_PREFIX + key, json.dumps({"raw_text": raw_text, "call": call})
        )


# (call_result, purpose, error) -> None ; writes the llm_calls ledger row
LedgerRecorder = Callable[[GeminiCallResult, str, str | None], Awaitable[None]]


async def _noop_ledger(call: GeminiCallResult, purpose: str, error: str | None) -> None:
    return None


class TokenBucketLimiter:
    """Global Redis token bucket (real; best-effort — never fails a run).

    Uses a Lua script for atomic refill+take. Without Redis (or on any error)
    the limiter allows the call — rate limiting protects the API quota, it is
    not a correctness gate.
    """

    _LUA = """
    local key = KEYS[1]
    local rate = tonumber(ARGV[1])
    local capacity = tonumber(ARGV[2])
    local now = tonumber(ARGV[3])
    local data = redis.call('HMGET', key, 'tokens', 'ts')
    local tokens = tonumber(data[1])
    local ts = tonumber(data[2])
    if tokens == nil then tokens = capacity; ts = now end
    local delta = math.max(0, now - ts)
    tokens = math.min(capacity, tokens + delta * rate)
    local allowed = 0
    if tokens >= 1 then tokens = tokens - 1; allowed = 1 end
    redis.call('HMSET', key, 'tokens', tokens, 'ts', now)
    redis.call('EXPIRE', key, 120)
    return allowed
    """

    def __init__(self, redis: Any, *, rate_per_sec: float = 8.0, capacity: int = 16) -> None:
        self._redis = redis
        self._rate = rate_per_sec
        self._capacity = capacity
        self._key = "llm:ratebucket:global"

    async def acquire(self, *, max_wait_s: float = 30.0) -> None:
        deadline = time.monotonic() + max_wait_s
        while True:
            try:
                allowed = await self._redis.eval(
                    self._LUA, 1, self._key, self._rate, self._capacity, time.time()
                )
            except Exception as exc:  # limiter must never break a run
                logger.warning("rate_limiter_unavailable", error=str(exc))
                return
            if int(allowed) == 1:
                return
            if time.monotonic() >= deadline:
                logger.warning("rate_limiter_timeout")
                return
            await asyncio.sleep(1.0 / max(self._rate, 1.0))


class LLMGateway:
    """Cache + rate-limit + replay + ledger for one physical model call."""

    def __init__(
        self,
        gemini: GeminiClient,
        *,
        redis: Any | None = None,
        mode: LLMMode = LLMMode.LIVE,
        replay_store: ReplayStore | None = None,
        ledger: LedgerRecorder | None = None,
        limiter: TokenBucketLimiter | None = None,
    ) -> None:
        self._gemini = gemini
        self._redis = redis
        self._mode = mode
        self._replay = replay_store
        self._ledger = ledger or _noop_ledger
        self._limiter = limiter or (TokenBucketLimiter(redis) if redis is not None else None)

    @property
    def mode(self) -> LLMMode:
        return self._mode

    async def complete(
        self, request: LLMRequest, model: str, *, governor: CostGovernor | None = None
    ) -> LLMResult:
        """Execute (or replay) one validated structured call.

        Order: replay store (REPLAY) → Redis cache → rate limit → API →
        record. Raises the SDK's :class:`ValidationError` on a malformed
        response (the caller's repair loop owns the retry) and
        :class:`GeminiUnconfiguredError` when a live call has no key.
        """
        key = request.fingerprint(model)

        if self._mode is LLMMode.REPLAY:
            served = await self._from_replay(request, key)
            if served is None:
                raise LLMReplayMissError(
                    f"No recorded response for {request.purpose!r} ({key[:12]}…)"
                )
            await self._ledger(served.call, request.purpose, None)
            return served

        cached = await self._from_cache(request, key)
        if cached is not None:
            await self._ledger(cached.call, request.purpose, None)
            return cached

        if governor is not None and not governor.escalation_allowed() and model == self._gemini_pro:
            logger.warning("cost_governor_blocks_pro", purpose=request.purpose)

        if self._limiter is not None:
            await self._limiter.acquire()

        try:
            parsed, call = await self._gemini.generate_structured(
                schema=request.schema_type,
                prompt=request.prompt,
                image_png=request.image_png,
                model=model,
                temperature=request.temperature,
            )
        except GeminiUnconfiguredError:
            raise
        except Exception as exc:
            await self._ledger(GeminiCallResult(model=model), request.purpose, str(exc)[:500])
            raise

        raw_text = parsed.model_dump_json()
        result = LLMResult(parsed=parsed, raw_text=raw_text, call=call)

        if governor is not None:
            governor.charge(call.cost_usd)
        await self._store_cache(key, result)
        if self._mode is LLMMode.RECORD and self._replay is not None:
            await self._replay.put(key, raw_text, call.model_dump())
        await self._ledger(call, request.purpose, None)
        return result

    @property
    def _gemini_pro(self) -> str:
        return self._gemini._config.pro_model  # noqa: SLF001 - same package intent

    async def _from_replay(self, request: LLMRequest, key: str) -> LLMResult | None:
        if self._replay is None:
            return None
        found = await self._replay.get(key)
        if found is None:
            return None
        raw_text, call_dict = found
        parsed = request.schema_type.model_validate_json(raw_text)
        return LLMResult(
            parsed=parsed,
            raw_text=raw_text,
            call=GeminiCallResult.model_validate(call_dict),
            replayed=True,
        )

    async def _from_cache(self, request: LLMRequest, key: str) -> LLMResult | None:
        if self._redis is None:
            return None
        import json

        try:
            raw = await self._redis.get(_CACHE_PREFIX + key)
        except Exception:
            return None
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
            parsed = request.schema_type.model_validate_json(payload["raw_text"])
        except (ValidationError, ValueError, KeyError):
            return None
        call = GeminiCallResult.model_validate(payload["call"])
        call = call.model_copy(update={"cost_usd": 0.0})  # a cache hit costs nothing
        return LLMResult(parsed=parsed, raw_text=payload["raw_text"], call=call, cache_hit=True)

    async def _store_cache(self, key: str, result: LLMResult) -> None:
        if self._redis is None:
            return
        import json

        try:
            await self._redis.set(
                _CACHE_PREFIX + key,
                json.dumps({"raw_text": result.raw_text, "call": result.call.model_dump()}),
                ex=_CACHE_TTL_SECONDS,
            )
        except Exception as exc:  # cache write failure is never fatal
            logger.warning("llm_cache_write_failed", error=str(exc))


class LLMReplayMissError(AppError):
    """REPLAY mode had no recorded response for a request."""

    code = "llm_replay_miss"
    status_code = 503
