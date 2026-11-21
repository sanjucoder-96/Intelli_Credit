"""Phase-9 demo-reliability execution-mode signal (item 11).

The system must be TRUTHFUL about whether a run was LIVE, served from REPLAY, or
DEGRADED (a live call that fell back to a recording) — and must never present a
degraded/fallback answer as a clean live result.
"""

from __future__ import annotations

from app.application.execution_mode import resolve_execution_mode


def test_live_mode_without_degradation() -> None:
    assert resolve_execution_mode("live", degraded=False) == "LIVE"


def test_live_run_with_fallback_is_degraded_not_live() -> None:
    # A transient-503 replay fallback during a LIVE run must surface as DEGRADED.
    assert resolve_execution_mode("live", degraded=True) == "DEGRADED"


def test_configured_replay_is_replay_even_if_flagged() -> None:
    # An intentionally-offline REPLAY run is REPLAY, not DEGRADED.
    assert resolve_execution_mode("replay", degraded=False) == "REPLAY"
    assert resolve_execution_mode("replay", degraded=True) == "REPLAY"
    assert resolve_execution_mode("REPLAY", degraded=False) == "REPLAY"  # case-insensitive


def test_record_mode_behaves_like_live() -> None:
    assert resolve_execution_mode("record", degraded=False) == "LIVE"
    assert resolve_execution_mode("record", degraded=True) == "DEGRADED"
