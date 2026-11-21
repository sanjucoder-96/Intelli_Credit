"""Agent output contracts and the confidence algebra (ARCHITECTURE §5).

Confidence is computed, not vibes: a weighted geometric mean of measured
components so one weak leg sinks the score. Edge rules are part of the
versioned ``wgm-v1`` method: components are epsilon-floored (``max(c, 0.05)``)
so a weak leg can never zero the score outright, and undefined components
(e.g. tool success rate for an agent that made no tool calls) are omitted
with their weight renormalized over the components that remain.
"""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field

CONFIDENCE_METHOD = "wgm-v1"
_EPSILON_FLOOR = 0.05


class ConfidenceReport(BaseModel):
    """Score plus the inspectable components it was computed from."""

    score: float = Field(ge=0.0, le=1.0)
    components: dict[str, float] = Field(default_factory=dict)
    method: str = CONFIDENCE_METHOD


class Critique(BaseModel):
    """Structured reflection outcome (rubric critic lands in Phase 5)."""

    passed: bool
    rubric_scores: dict[str, float] = Field(default_factory=dict)
    validator_pass_ratio: float | None = None
    issues: list[str] = Field(default_factory=list)
    revise_instructions: str | None = None


class AgentResult(BaseModel):
    """What every agent returns from its lifecycle run."""

    output: Any
    confidence: ConfidenceReport
    citations: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def compute_confidence(
    weights: dict[str, float],
    components: dict[str, float],
    *,
    floor: float = _EPSILON_FLOOR,
    method: str = CONFIDENCE_METHOD,
) -> ConfidenceReport:
    """Weighted geometric mean of ``components`` under ``weights``.

    Only components that appear in both mappings (with positive weight)
    participate; their weights are renormalized so omission never inflates
    the score. Values are floored at ``floor`` before the log so a zero
    component sinks — but never annihilates — the total. With no usable
    components the score is 0.0 by definition: an agent that measured
    nothing has earned no confidence.
    """
    usable = {
        key: max(value, floor)
        for key, value in components.items()
        if key in weights and weights[key] > 0
    }
    if not usable:
        return ConfidenceReport(score=0.0, components=dict(components), method=method)

    total_weight = sum(weights[key] for key in usable)
    log_sum = sum((weights[key] / total_weight) * math.log(value) for key, value in usable.items())
    score = min(1.0, max(0.0, math.exp(log_sum)))
    return ConfidenceReport(score=score, components=dict(components), method=method)
