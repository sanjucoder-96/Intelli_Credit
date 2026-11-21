"""Phase-9 research-agent benchmark (item 5).

A small, reproducible LABELLED benchmark for the external-research agent. Web
search is Tavily- and LLM-gated, so live recall/precision are UNMEASURED
without a key — and are never fabricated here. What IS asserted deterministically
(and always) is the agent's safety + honest-degradation contract; what WOULD be
measured with a key (relevance, source quality, citation correctness, finding
classification, temporal freshness) is defined by the labelled queries below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ResearchQuery:
    query_id: str
    category: str  # adverse_news | litigation | promoter | regulatory | sector | corporate_event
    entity: str
    prompt: str
    # Expected signal, where known — used to score relevance/classification when
    # the benchmark is run live. Kept modest; not every query has a ground truth.
    expected_min_severity: str | None = None  # none|low|medium|high|critical
    expected_source_hints: tuple[str, ...] = field(default_factory=tuple)


# Six labelled queries spanning the categories the research agent covers.
RESEARCH_QUERIES: tuple[ResearchQuery, ...] = (
    ResearchQuery(
        "q1",
        "adverse_news",
        "Acme Textiles Pvt Ltd",
        "recent adverse news, defaults, or financial distress for Acme Textiles",
        expected_min_severity=None,
    ),
    ResearchQuery(
        "q2",
        "litigation",
        "Deccan Agro Foods Ltd",
        "pending litigation, NCLT/DRT or insolvency proceedings for Deccan Agro Foods",
        expected_min_severity=None,
        expected_source_hints=("nclt", "indiankanoon"),
    ),
    ResearchQuery(
        "q3",
        "promoter",
        "Deccan Agro Foods Ltd",
        "promoter background, wilful-defaulter or disqualification flags for the promoters",
        expected_min_severity=None,
    ),
    ResearchQuery(
        "q4",
        "regulatory",
        "Acme Textiles Pvt Ltd",
        "SEBI/RBI/GST regulatory action, penalties, or show-cause notices",
        expected_min_severity=None,
        expected_source_hints=("sebi", "rbi"),
    ),
    ResearchQuery(
        "q5",
        "sector",
        "Indian textiles sector",
        "sector headwinds, demand slowdown, or input-cost pressure for Indian textiles",
        expected_min_severity=None,
    ),
    ResearchQuery(
        "q6",
        "corporate_event",
        "Acme Textiles Pvt Ltd",
        "recent corporate events: capacity expansion, fund-raise, management change",
        expected_min_severity=None,
    ),
)


def run_research_benchmark(configured: bool = False) -> dict[str, Any]:
    """Return the benchmark result — measured only when web research is configured.

    Metrics that a live run WOULD produce (never fabricated here):
      relevance, source_quality, citation_correctness, finding_classification,
      temporal_freshness.
    """
    categories = sorted({q.category for q in RESEARCH_QUERIES})
    if not configured:
        return {
            "measured": False,
            "reason": (
                "Live web-search recall/precision requires IC_TAVILY__API_KEY + "
                "IC_GEMINI__API_KEY; not claimed without measurement."
            ),
            "queries": len(RESEARCH_QUERIES),
            "categories": categories,
            "would_measure": [
                "relevance",
                "source_quality",
                "citation_correctness",
                "finding_classification",
                "temporal_freshness",
            ],
        }
    return {
        "measured": True,
        "queries": len(RESEARCH_QUERIES),
        "categories": categories,
        "note": "Live scoring is executed by the harness with the configured research client.",
    }
