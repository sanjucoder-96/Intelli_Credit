"""Phase-8 Gemini failure handling (priority #5).

Deterministic proof that a transient provider failure degrades gracefully and
NEVER fabricates an answer:

    transient failure → retry → retry → (replay fallback | honest error)

Covered: every retriable status code (408/429/500/502/503/504), timeout/overload
markers, non-transient errors (not retried), retry exhaustion, replay fallback
with an explicit ledger tag, REPLAY-mode miss, and the StructuredCaller repair→
escalate ladder for malformed structured responses. All offline (fake client,
zeroed backoff so retries do not actually sleep).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from app.agents.llm import (
    InMemoryReplayStore,
    LLMGateway,
    LLMMode,
    LLMReplayMissError,
    LLMRequest,
    _is_transient,
)
from app.agents.spec import ModelPolicy, RetryPolicy
from app.agents.structured import StructuredCaller, StructuredCallFailedError
from app.core.config import GeminiConfig
from app.infrastructure.gemini import GeminiCallResult, GeminiUnconfiguredError


class Answer(BaseModel):
    verdict: str
    score: int


class _CodedError(Exception):
    """A provider error carrying an HTTP-like ``code`` attribute."""

    def __init__(self, message: str, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class RaisingClient:
    """Fake GeminiClient that raises ``error`` for the first ``fail_times`` calls."""

    def __init__(self, *, error: Exception, fail_times: int) -> None:
        self._config = GeminiConfig(flash_model="gemini-flash", pro_model="gemini-pro")
        self.configured = True
        self._error = error
        self._fail_times = fail_times
        self.calls = 0

    async def generate_structured(
        self,
        *,
        schema: type[BaseModel],
        prompt: str,
        image_png: bytes | None = None,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> tuple[BaseModel, GeminiCallResult]:
        self.calls += 1
        if self.calls <= self._fail_times:
            raise self._error
        parsed = schema.model_validate({"verdict": "approve", "score": 7})
        return parsed, GeminiCallResult(model=model or "gemini-flash", cost_usd=0.001)


def _request() -> LLMRequest:
    return LLMRequest(schema_type=Answer, prompt="Assess Acme.", purpose="assess")


def _ledger() -> tuple[list[tuple[str, str | None]], Any]:
    seen: list[tuple[str, str | None]] = []

    async def record(call: GeminiCallResult, purpose: str, error: str | None) -> None:
        seen.append((purpose, error))

    return seen, record


def _gateway(client: Any, **kw: Any) -> LLMGateway:
    # Zero backoff keeps the retry loop instantaneous in tests.
    kw.setdefault("transient_backoff_base", 0.0)
    return LLMGateway(client, mode=LLMMode.LIVE, **kw)


# --------------------------------------------------------------------------- #
# _is_transient classification
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
def test_transient_status_codes_are_retriable(code: int) -> None:
    assert _is_transient(_CodedError("boom", code=code)) is True


@pytest.mark.parametrize(
    "message",
    [
        "503 UNAVAILABLE: high demand",
        "the model is overloaded, try again later",
        "RESOURCE_EXHAUSTED",
        "deadline exceeded",
        "temporarily unavailable",
    ],
)
def test_transient_markers_in_message_are_retriable(message: str) -> None:
    assert _is_transient(Exception(message)) is True


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_error_codes_are_not_retriable(code: int) -> None:
    assert _is_transient(_CodedError("bad request", code=code)) is False


# --------------------------------------------------------------------------- #
# Retry → success, and exhaustion → honest error (never fabricate)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("code", [408, 429, 500, 502, 503, 504])
async def test_transient_error_retried_then_succeeds(code: int) -> None:
    client = RaisingClient(error=_CodedError("transient", code=code), fail_times=2)
    seen, record = _ledger()
    gateway = _gateway(client, ledger=record, max_transient_retries=3)

    result = await gateway.complete(_request(), "gemini-flash")

    assert result.parsed.model_dump() == {"verdict": "approve", "score": 7}
    assert client.calls == 3  # two failures then a success
    assert seen[-1] == ("assess", None)  # success recorded, no error


async def test_timeout_marker_is_retried_then_succeeds() -> None:
    client = RaisingClient(error=Exception("504 deadline exceeded"), fail_times=1)
    gateway = _gateway(client, max_transient_retries=3)
    result = await gateway.complete(_request(), "gemini-flash")
    assert result.parsed.score == 7
    assert client.calls == 2


async def test_exhausted_retries_reraise_and_do_not_fabricate() -> None:
    # Fails forever; with no replay recording the gateway MUST raise, never
    # invent a parsed answer.
    client = RaisingClient(error=_CodedError("still down", code=503), fail_times=99)
    seen, record = _ledger()
    gateway = _gateway(client, ledger=record, max_transient_retries=2)

    with pytest.raises(_CodedError):
        await gateway.complete(_request(), "gemini-flash")

    assert client.calls == 3  # initial + 2 retries
    assert seen[-1][0] == "assess" and seen[-1][1] is not None  # error recorded


async def test_non_transient_error_is_not_retried() -> None:
    client = RaisingClient(error=_CodedError("bad request", code=400), fail_times=99)
    gateway = _gateway(client, max_transient_retries=3)
    with pytest.raises(_CodedError):
        await gateway.complete(_request(), "gemini-flash")
    assert client.calls == 1  # no retry on a 400


async def test_unconfigured_error_is_not_retried() -> None:
    client = RaisingClient(error=GeminiUnconfiguredError("no key"), fail_times=99)
    gateway = _gateway(client, max_transient_retries=3)
    with pytest.raises(GeminiUnconfiguredError):
        await gateway.complete(_request(), "gemini-flash")
    assert client.calls == 1


# --------------------------------------------------------------------------- #
# Replay fallback — explicit, tagged, never silent
# --------------------------------------------------------------------------- #


async def test_transient_exhaustion_falls_back_to_replay_with_explicit_tag() -> None:
    store = InMemoryReplayStore()
    # First RECORD a good response so a recording exists for this exact request.
    recorder = LLMGateway(
        RaisingClient(error=Exception("unused"), fail_times=0),
        mode=LLMMode.RECORD,
        replay_store=store,
    )
    recorded = await recorder.complete(_request(), "gemini-flash")

    # Now a LIVE gateway whose client is permanently down falls back to replay.
    seen, record = _ledger()
    gateway = LLMGateway(
        RaisingClient(error=_CodedError("down", code=503), fail_times=99),
        mode=LLMMode.LIVE,
        replay_store=store,
        ledger=record,
        transient_backoff_base=0.0,
        max_transient_retries=1,
    )
    served = await gateway.complete(_request(), "gemini-flash")

    assert served.replayed is True
    assert served.parsed.model_dump() == recorded.parsed.model_dump()
    # The mode is NEVER hidden: the ledger row is tagged explicitly.
    assert ("assess", "served_from_replay_fallback") in seen


async def test_transient_exhaustion_without_replay_raises() -> None:
    # Same permanent outage but NO recording → must raise, not fabricate.
    gateway = LLMGateway(
        RaisingClient(error=_CodedError("down", code=503), fail_times=99),
        mode=LLMMode.LIVE,
        replay_store=InMemoryReplayStore(),
        transient_backoff_base=0.0,
        max_transient_retries=1,
    )
    with pytest.raises(_CodedError):
        await gateway.complete(_request(), "gemini-flash")


async def test_replay_mode_miss_raises_typed_error() -> None:
    gateway = LLMGateway(
        RaisingClient(error=Exception("unused"), fail_times=0),
        mode=LLMMode.REPLAY,
        replay_store=InMemoryReplayStore(),
    )
    with pytest.raises(LLMReplayMissError):
        await gateway.complete(_request(), "gemini-flash")


# --------------------------------------------------------------------------- #
# StructuredCaller repair→escalate ladder for malformed responses
# --------------------------------------------------------------------------- #


class MalformedClient:
    """Returns invalid JSON (missing required fields) for the first N calls."""

    def __init__(self, *, bad_first: int) -> None:
        self._config = GeminiConfig(flash_model="gemini-flash", pro_model="gemini-pro")
        self.configured = True
        self._bad_first = bad_first
        self.calls = 0

    async def generate_structured(
        self,
        *,
        schema: type[BaseModel],
        prompt: str,
        image_png: bytes | None = None,
        model: str | None = None,
        temperature: float = 0.1,
    ) -> tuple[BaseModel, GeminiCallResult]:
        self.calls += 1
        if self.calls <= self._bad_first:
            schema.model_validate({})  # raises ValidationError (missing fields)
        parsed = schema.model_validate({"verdict": "approve", "score": 7})
        return parsed, GeminiCallResult(model=model or "gemini-flash", cost_usd=0.001)


def _caller(client: Any) -> StructuredCaller:
    gateway = LLMGateway(client, mode=LLMMode.LIVE, transient_backoff_base=0.0)
    policy = ModelPolicy(primary="gemini-flash", escalate_to="gemini-pro", temperature=0.1)
    return StructuredCaller(gateway, policy, RetryPolicy())


async def test_malformed_response_is_repaired_then_succeeds() -> None:
    client = MalformedClient(bad_first=2)  # two bad, then good — within repair budget
    result = await _caller(client).call(_request())
    assert result.parsed.model_dump() == {"verdict": "approve", "score": 7}
    assert client.calls >= 3


async def test_persistently_malformed_response_fails_loudly_never_fabricates() -> None:
    client = MalformedClient(bad_first=99)  # never returns valid JSON
    with pytest.raises(StructuredCallFailedError):
        await _caller(client).call(_request())
    # 3 Flash (repair_max+1) + 2 Pro (regen_max+1) = 5 physical attempts, then error.
    assert client.calls == 5
