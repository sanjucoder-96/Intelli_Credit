"""Phase-9 extraction + research benchmarks (items 4 & 5).

Asserts the deterministic extraction tier reports REAL metrics with confidence
intervals and zero fabrication, and that the LLM extraction tier and the
research benchmark honestly report UNMEASURED when no API key is present — a
fabricated accuracy number must never appear.
"""

from __future__ import annotations

from eval.extraction_benchmark import (
    FINANCIAL_CORPUS,
    llm_financial_benchmark,
    run_deterministic_benchmark,
    wilson_interval,
)
from eval.research_benchmark import RESEARCH_QUERIES, run_research_benchmark


def test_wilson_interval_is_sane() -> None:
    lo, hi = wilson_interval(10, 10)  # 10/10 → high but < 1 lower bound (honest for small n)
    assert 0.6 < lo <= 1.0 and hi == 1.0
    lo0, hi0 = wilson_interval(0, 0)
    assert (lo0, hi0) == (0.0, 1.0)  # no data → full interval
    lo5, hi5 = wilson_interval(5, 10)
    assert lo5 < 0.5 < hi5  # symmetric-ish around 0.5, wide for n=10


def test_deterministic_extraction_benchmark_reports_real_metrics() -> None:
    summary = run_deterministic_benchmark().summary()
    assert summary["measured"] is True
    assert summary["classification_accuracy"] == 1.0
    assert summary["field_recall"] == 1.0
    assert summary["numeric_accuracy"] == 1.0
    assert summary["fabrications"] == 0
    # Confidence intervals are present and bracket the point estimate.
    lo, hi = summary["field_recall_ci95"]
    assert 0.0 <= lo <= 1.0 and 0.0 <= hi <= 1.0 and lo <= summary["field_recall"] <= hi
    assert summary["known_fields_n"] >= 10


def test_llm_financial_tier_is_unmeasured_without_a_key() -> None:
    result = llm_financial_benchmark(gemini=None)
    assert result["measured"] is False
    assert "IC_GEMINI__API_KEY" in result["reason"]
    # The labelled corpus exists so it CAN be measured with a key.
    assert result["corpus_size"] == len(FINANCIAL_CORPUS) >= 2
    assert result["labelled_fields"] > 0


class _ConfiguredClient:
    configured = True


def test_llm_financial_tier_marks_measured_when_a_client_is_configured() -> None:
    result = llm_financial_benchmark(gemini=_ConfiguredClient())
    assert result["measured"] is True
    assert result["corpus_size"] == len(FINANCIAL_CORPUS)


def test_research_benchmark_is_unmeasured_without_a_key_and_well_formed() -> None:
    result = run_research_benchmark(configured=False)
    assert result["measured"] is False
    assert result["queries"] == len(RESEARCH_QUERIES) == 6
    # Covers the six required categories.
    assert set(result["categories"]) == {
        "adverse_news",
        "litigation",
        "promoter",
        "regulatory",
        "sector",
        "corporate_event",
    }
    assert "relevance" in result["would_measure"]


def test_research_benchmark_reports_measured_when_configured() -> None:
    assert run_research_benchmark(configured=True)["measured"] is True
