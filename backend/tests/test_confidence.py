"""The wgm-v1 confidence algebra: math, floors, omission, edge cases."""

from __future__ import annotations

import math

from app.agents.results import compute_confidence


def test_weighted_geometric_mean_exact() -> None:
    report = compute_confidence(
        {"critic": 0.5, "evidence": 0.5},
        {"critic": 0.8, "evidence": 0.2},
    )
    assert math.isclose(report.score, math.sqrt(0.8 * 0.2), rel_tol=1e-9)
    assert report.method == "wgm-v1"


def test_one_weak_leg_sinks_the_score() -> None:
    strong = compute_confidence({"critic": 0.5, "evidence": 0.5}, {"critic": 0.9, "evidence": 0.9})
    weak_leg = compute_confidence(
        {"critic": 0.5, "evidence": 0.5}, {"critic": 0.9, "evidence": 0.1}
    )
    assert weak_leg.score < strong.score * 0.5


def test_zero_component_is_floored_not_annihilating() -> None:
    report = compute_confidence(
        {"critic": 0.5, "evidence": 0.5},
        {"critic": 1.0, "evidence": 0.0},
    )
    assert math.isclose(report.score, math.sqrt(1.0 * 0.05), rel_tol=1e-9)
    assert report.score > 0.0


def test_missing_components_renormalize_weights() -> None:
    report = compute_confidence(
        {"critic": 0.25, "evidence": 0.30, "tool": 0.45},
        {"critic": 0.8},
    )
    # Only "critic" participates; its weight renormalizes to 1.0.
    assert math.isclose(report.score, 0.8, rel_tol=1e-9)


def test_unknown_components_are_ignored() -> None:
    report = compute_confidence({"critic": 1.0}, {"critic": 0.7, "mystery": 0.01})
    assert math.isclose(report.score, 0.7, rel_tol=1e-9)


def test_no_usable_components_scores_zero() -> None:
    report = compute_confidence({"critic": 1.0}, {})
    assert report.score == 0.0


def test_components_are_preserved_for_inspection() -> None:
    components = {"critic": 0.8, "evidence": 0.0}
    report = compute_confidence({"critic": 0.5, "evidence": 0.5}, components)
    # Raw (pre-floor) components stay inspectable so the UI can show *why*.
    assert report.components == components
