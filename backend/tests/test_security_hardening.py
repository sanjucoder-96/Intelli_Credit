"""Phase-9 security hardening regressions.

Locks in the two fixes for the Phase-8 observations:

1. trace redaction is now RECURSIVE — a secret nested inside dicts / lists /
   tuples of a trace payload is dropped, not just top-level keys;
2. the SSE ``/cases/{id}/events`` stream now requires authentication + the
   CASE_READ permission (uniform with every other case route), so a valid token
   for the wrong tenant — or no token at all — cannot subscribe.
"""

from __future__ import annotations

import json
from uuid import uuid4

from httpx import AsyncClient

from app.agents.trace import _REDACT_KEYS, _redact
from app.api.routers.cases import router as cases_router

# --------------------------------------------------------------------------- #
# 1. Recursive redaction.
# --------------------------------------------------------------------------- #


def test_redaction_drops_secrets_at_every_nesting_depth() -> None:
    payload = {
        "model": "gemini-flash",
        "api_key": "TOPSECRET_TOP",
        "nested": {
            "password": "hunter2",
            "safe": 1,
            "deeper": {"token": "tok_abc", "ok": True, "authorization": "Bearer xyz"},
        },
        "list_of_dicts": [
            {"secret": "s1", "keep": "v1"},
            {"access_token": "at_2", "n": 2},
        ],
        "tuple_payload": ({"jwt_secret": "jjj"}, "plain"),
        "findings": [{"data": {"api_key": "leak_in_finding", "value": 5}}],
    }
    redacted = _redact(payload)
    flat = json.dumps(redacted)

    for secret in (
        "TOPSECRET_TOP",
        "hunter2",
        "tok_abc",
        "Bearer xyz",
        "s1",
        "at_2",
        "jjj",
        "leak_in_finding",
    ):
        assert secret not in flat, f"secret {secret!r} leaked through redaction"

    # Non-secret data is preserved at every depth.
    assert redacted["model"] == "gemini-flash"
    assert redacted["nested"]["safe"] == 1
    assert redacted["nested"]["deeper"]["ok"] is True
    assert redacted["list_of_dicts"][0]["keep"] == "v1"
    assert redacted["list_of_dicts"][1]["n"] == 2
    assert redacted["findings"][0]["data"]["value"] == 5
    # The tuple stays a tuple, with its secret sub-dict scrubbed.
    assert isinstance(redacted["tuple_payload"], tuple)
    assert redacted["tuple_payload"][1] == "plain"
    assert "jwt_secret" not in redacted["tuple_payload"][0]


def test_redaction_is_case_insensitive_on_keys() -> None:
    redacted = _redact({"API_KEY": "x", "Authorization": "Bearer y", "Model": "flash"})
    assert "API_KEY" not in redacted and "Authorization" not in redacted
    assert redacted["Model"] == "flash"  # non-secret keys keep their original case


def test_redact_keys_still_cover_the_phase8_baseline() -> None:
    assert {"prompt", "api_key", "password", "token"} <= _REDACT_KEYS


# --------------------------------------------------------------------------- #
# 2. SSE /events authorization.
# --------------------------------------------------------------------------- #


def test_sse_events_route_declares_a_permission_guard() -> None:
    route = next(
        r for r in cases_router.routes if getattr(r, "path", "") == "/cases/{case_id}/events"
    )
    # The fix added a route-level require_permissions(CASE_READ) dependency; the
    # flattened dependant must reference a permission check (not just get_current_user).
    calls = " ".join(getattr(d.call, "__qualname__", "") for d in route.dependant.dependencies)
    assert "require_permissions" in calls, (
        f"SSE /events must declare a permission guard; got: {calls}"
    )


async def test_sse_events_requires_authentication(client: AsyncClient) -> None:
    # No bearer token → 401 before any case lookup (the stream is not open).
    response = await client.get(f"/api/v1/cases/{uuid4()}/events")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
