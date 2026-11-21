"""Execution-mode resolution for the demo-reliability signal (Phase 9).

A run is one of three modes, and the system is always TRUTHFUL about which:

- ``LIVE``     — LLM calls hit the real provider.
- ``REPLAY``   — the gateway is configured to serve recorded fixtures only
  (offline demo / CI); no live provider call is made.
- ``DEGRADED`` — a LIVE run in which at least one call could not complete live
  and was served from a recorded fallback (tagged ``served_from_replay_fallback``
  in the ledger) or a non-required node degraded. The answer is NEVER fabricated
  — degradation is surfaced, not hidden.

This is a pure function of the configured mode and a per-run degradation flag;
the caller computes ``degraded`` from the ledger / execution error.
"""

from __future__ import annotations

from typing import Literal

ExecutionMode = Literal["LIVE", "REPLAY", "DEGRADED"]


def resolve_execution_mode(llm_mode: str, *, degraded: bool) -> ExecutionMode:
    """Map the configured LLM mode + a degradation flag to the demo mode.

    A configured REPLAY run is intentionally offline (REPLAY, not DEGRADED); a
    LIVE run that fell back to a recording is DEGRADED; otherwise LIVE.
    """
    if llm_mode.lower() == "replay":
        return "REPLAY"
    return "DEGRADED" if degraded else "LIVE"
