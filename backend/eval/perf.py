"""Deterministic performance baselines (Phase-8 item 13).

Measures the latency of IntelliCredit's synchronous hot paths — extraction,
ratio+scorecard evaluation, PD feature build, and CAM assemble+render. These
are baseline measurements (not optimization targets): the acceptance report
records them and the perf test asserts only generous ceilings to catch a gross
regression.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from decimal import Decimal
from statistics import median

from app.analysis.cam import CamFinding, CompanyProfile, assemble_cam, render_markdown
from app.analysis.features import build_pd_features, feature_row
from app.analysis.five_cs import derive_five_cs
from app.analysis.ratios import compute_ratios
from app.analysis.scorecard import ScoreInputs, credit_appraisal_scorecard, evaluate_scorecard
from app.docintel.extractors.gstr3b import extract_gstr3b
from app.docintel.schemas import FinancialLineItems

_GSTR3B = "\n".join(
    [
        "FORM GSTR-3B",
        "GSTIN 27AAACA1234A1Z5",
        "Period 04-2026",
        "3.1 Details of Outward Supplies",
        "(a) Outward taxable supplies (other than zero rated) 1,00,00,000.00",
        "Integrated Tax 9,00,000.00",
        "(A) ITC Available - All other ITC - IGST 5,00,000.00",
        "Tax paid in cash 4,00,000.00",
    ]
)

_FIN = FinancialLineItems(
    fiscal_year="2025-26",
    revenue=Decimal("100000000"),
    ebitda=Decimal("18000000"),
    net_profit=Decimal("9000000"),
    total_assets=Decimal("120000000"),
    total_equity=Decimal("60000000"),
    total_debt=Decimal("30000000"),
    current_assets=Decimal("50000000"),
    current_liabilities=Decimal("25000000"),
    inventory=Decimal("15000000"),
    receivables=Decimal("20000000"),
    interest_expense=Decimal("3000000"),
    cash_flow_from_operations=Decimal("15000000"),
)


def _time_ms(fn: Callable[[], object], iterations: int) -> dict[str, float]:
    samples: list[float] = []
    for _ in range(iterations):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    return {
        "iterations": iterations,
        "p50_ms": round(median(samples), 3),
        "p95_ms": round(samples[min(len(samples) - 1, int(len(samples) * 0.95))], 3),
        "max_ms": round(samples[-1], 3),
    }


def _extract() -> None:
    extract_gstr3b([_GSTR3B])


def _score() -> None:
    ratios = compute_ratios(_FIN)
    inputs = ScoreInputs(
        ratios=ratios.available_ratios(), financials=_FIN, turnover=_FIN.revenue, ml_pd=0.03
    )
    evaluate_scorecard(credit_appraisal_scorecard(), inputs)


def _features() -> None:
    feature_row(build_pd_features(_FIN))


def _cam() -> None:
    ratios = compute_ratios(_FIN)
    inputs = ScoreInputs(
        ratios=ratios.available_ratios(), financials=_FIN, turnover=_FIN.revenue, ml_pd=0.03
    )
    risk = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    five = derive_five_cs(risk, ratios, inputs)
    cam = assemble_cam(
        company=CompanyProfile(legal_name="Acme"),
        risk=risk,
        ratios=ratios,
        five_cs=five,
        findings=[CamFinding(title="x", severity="low")],
    )
    render_markdown(cam)


def collect_perf_baseline(iterations: int = 200) -> dict[str, dict[str, float]]:
    """Measure every deterministic hot path; returns name → latency stats."""
    return {
        "extraction_gstr3b": _time_ms(_extract, iterations),
        "ratios_scorecard": _time_ms(_score, iterations),
        "pd_feature_build": _time_ms(_features, iterations),
        "cam_assemble_render": _time_ms(_cam, iterations),
    }
