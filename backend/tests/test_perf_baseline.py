"""Phase-8 performance baseline (priority #9, item 13).

Establishes baseline latencies for the deterministic hot paths and asserts only
GENEROUS ceilings — the goal is a recorded baseline that flags a gross
regression, not an optimization target. Actual numbers are captured by the
acceptance report via ``eval.perf.collect_perf_baseline``.
"""

from __future__ import annotations

from eval.perf import collect_perf_baseline

# Generous per-call ceilings (ms). These are ~50-500x the observed p50 so only a
# gross regression trips them; they are NOT tuned targets.
_CEILINGS_MS = {
    "extraction_gstr3b": 100.0,
    "ratios_scorecard": 50.0,
    "pd_feature_build": 50.0,
    "cam_assemble_render": 100.0,
}


def test_hot_path_latency_baselines_are_within_generous_ceilings() -> None:
    baseline = collect_perf_baseline(iterations=100)
    for name, ceiling in _CEILINGS_MS.items():
        stats = baseline[name]
        assert stats["p50_ms"] < ceiling, f"{name} p50 {stats['p50_ms']}ms exceeded {ceiling}ms"


def test_baseline_report_is_serializable_for_the_acceptance_report() -> None:
    baseline = collect_perf_baseline(iterations=20)
    assert set(baseline) == set(_CEILINGS_MS)
    for stats in baseline.values():
        assert {"p50_ms", "p95_ms", "max_ms", "iterations"} <= set(stats)
