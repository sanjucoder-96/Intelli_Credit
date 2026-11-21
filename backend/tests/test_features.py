"""PD feature engineering (Phase 6): extraction from financials, the ordered
schema contract, missing-value handling, and coverage. Pure and deterministic —
no model, no DB.
"""

from __future__ import annotations

import math
from decimal import Decimal

from app.analysis.features import (
    FEATURE_NAMES,
    FEATURE_SCHEMA_VERSION,
    build_pd_features,
    feature_row,
)
from app.docintel.schemas import FinancialLineItems


def _strong() -> FinancialLineItems:
    return FinancialLineItems(
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


def test_feature_row_matches_schema_order_and_length() -> None:
    vec = build_pd_features(_strong())
    row = feature_row(vec)
    assert len(row) == len(FEATURE_NAMES) == 14
    assert vec.schema_version == FEATURE_SCHEMA_VERSION
    # Each position corresponds to its FEATURE_NAMES entry.
    for i, name in enumerate(FEATURE_NAMES):
        value = getattr(vec, name)
        if value is None:
            assert math.isnan(row[i])
        else:
            assert row[i] == float(value)


def test_features_are_computed_with_expected_values() -> None:
    vec = build_pd_features(_strong())
    assert vec.current_ratio is not None and abs(vec.current_ratio - 2.0) < 1e-6
    assert vec.roa is not None and abs(vec.roa - 0.075) < 1e-6  # 9M / 120M
    # liabilities = total_assets - total_equity = 120M - 60M = 60M; / 120M = 0.5
    # (matches the training definition; NOT interest-bearing total_debt 30M).
    assert vec.liabilities_to_assets is not None and abs(vec.liabilities_to_assets - 0.5) < 1e-6
    assert vec.equity_to_assets is not None and abs(vec.equity_to_assets - 0.5) < 1e-6
    # debt_to_equity feature = liabilities / equity = 60M / 60M = 1.0 (tol_tnw)
    assert vec.debt_to_equity is not None and abs(vec.debt_to_equity - 1.0) < 1e-6
    assert vec.asset_turnover is not None and abs(vec.asset_turnover - 0.8333333) < 1e-4
    # working capital = 50M - 25M = 25M; / 120M
    assert vec.working_capital_to_assets is not None
    assert abs(vec.working_capital_to_assets - 0.2083333) < 1e-4


def test_missing_inputs_yield_none_not_zero() -> None:
    # Only a revenue figure — most features cannot be computed.
    vec = build_pd_features(FinancialLineItems(revenue=Decimal("1000000")))
    assert vec.current_ratio is None  # no current assets/liabilities
    assert vec.roa is None  # no net profit / total assets
    row = feature_row(vec)
    assert all(
        math.isnan(x)
        for x, name in zip(row, FEATURE_NAMES, strict=True)
        if getattr(vec, name) is None
    )
    assert vec.coverage() < 0.2


def test_none_financials_is_empty_vector() -> None:
    vec = build_pd_features(None)
    assert vec.coverage() == 0.0
    assert all(math.isnan(x) for x in feature_row(vec))


def test_revenue_growth_needs_a_prior_period() -> None:
    fin = _strong()
    assert build_pd_features(fin).revenue_growth is None  # single period → missing
    prior = FinancialLineItems(fiscal_year="2024-25", revenue=Decimal("80000000"))
    grown = build_pd_features(fin, prior=prior)
    assert grown.revenue_growth is not None and abs(grown.revenue_growth - 0.25) < 1e-6


def test_coverage_reflects_populated_fraction() -> None:
    # Single-period strong company: every feature except revenue_growth.
    vec = build_pd_features(_strong())
    assert abs(vec.coverage() - 13 / 14) < 1e-9
