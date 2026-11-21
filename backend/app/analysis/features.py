"""Canonical PD feature vector — the one schema shared by training and inference.

The probability-of-default model (Phase 6, ADR-0010) consumes a fixed, ordered
vector of financial-ratio features. The exact same vector is:

- produced at **inference** by :func:`build_pd_features` from IntelliCredit's
  own :class:`~app.docintel.schemas.FinancialLineItems` (the numbers extracted
  from a borrower's statements), and
- produced at **training** by the ``ml`` package's Polish-dataset harmonizer,
  which maps the raw UCI ratios onto these same names with the same formulas.

Because both sides compute each feature with the identical definition, the model
IntelliCredit runs is exactly the model that was trained — no silent scale or
definition drift. Every feature here is computable from IntelliCredit's raw line
items; where a line item is absent for a given borrower the value is ``None`` and
the tree model routes the native NaN branch (invariant: honest missing-value
handling, never a fabricated fill). The ordered :data:`FEATURE_NAMES` and
:data:`FEATURE_SCHEMA_VERSION` are the contract; a model artifact records the
names it was trained on and inference refuses to load a mismatched artifact.

The model produces a *relative* default-risk signal only — it never sets a
score, limit, rate, or decision (those stay in the deterministic scorecard and
policy grid, ADR-0009). This module is pure and deterministic.
"""

from __future__ import annotations

import math
from decimal import Decimal

from pydantic import BaseModel, Field

from app.analysis.ratios import FinancialRatios, compute_ratios
from app.docintel.schemas import FinancialLineItems

FEATURE_SCHEMA_VERSION = "1.0"

# Ordered feature contract. Order is load-bearing: the model's feature matrix,
# the artifact's ``feature_list``, and :func:`feature_row` all follow it.
FEATURE_NAMES: tuple[str, ...] = (
    "roa",
    "net_margin",
    "ebitda_margin",
    "asset_turnover",
    "current_ratio",
    "quick_ratio",
    "liabilities_to_assets",
    "equity_to_assets",
    "debt_to_equity",
    "working_capital_to_assets",
    "ebitda_to_liabilities",
    "receivable_days",
    "inventory_days",
    "revenue_growth",
)

# Human-readable labels + orientation for reason-code rendering. ``higher_is_worse``
# marks features where a larger value raises default risk (leverage, days); the
# rest reduce risk as they grow. Used only for presentation — the model learns
# the true directions from data.
FEATURE_META: dict[str, tuple[str, bool]] = {
    "roa": ("Return on assets", False),
    "net_margin": ("Net profit margin", False),
    "ebitda_margin": ("EBITDA margin", False),
    "asset_turnover": ("Asset turnover", False),
    "current_ratio": ("Current ratio", False),
    "quick_ratio": ("Quick ratio", False),
    "liabilities_to_assets": ("Liabilities / assets (leverage)", True),
    "equity_to_assets": ("Equity / assets", False),
    "debt_to_equity": ("Liabilities / equity (leverage)", True),
    "working_capital_to_assets": ("Working capital / assets", False),
    "ebitda_to_liabilities": ("EBITDA / liabilities (debt service)", False),
    "receivable_days": ("Receivable days", True),
    "inventory_days": ("Inventory days", True),
    "revenue_growth": ("Revenue growth", False),
}


class PdFeatureVector(BaseModel):
    """The 14-dim PD feature vector; ``None`` means the input was unavailable."""

    roa: float | None = None
    net_margin: float | None = None
    ebitda_margin: float | None = None
    asset_turnover: float | None = None
    current_ratio: float | None = None
    quick_ratio: float | None = None
    liabilities_to_assets: float | None = None
    equity_to_assets: float | None = None
    debt_to_equity: float | None = None
    working_capital_to_assets: float | None = None
    ebitda_to_liabilities: float | None = None
    receivable_days: float | None = None
    inventory_days: float | None = None
    revenue_growth: float | None = None

    schema_version: str = Field(default=FEATURE_SCHEMA_VERSION)

    def coverage(self) -> float:
        """Fraction of the vector that is populated (non-missing)."""
        present = sum(getattr(self, name) is not None for name in FEATURE_NAMES)
        return present / len(FEATURE_NAMES)


def _f(value: Decimal | float | None) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _div(num: Decimal | float | None, den: Decimal | float | None) -> float | None:
    """Safe ratio: ``None`` when either side is missing or the denominator is ~0."""
    n, d = _f(num), _f(den)
    if n is None or d is None or abs(d) < 1e-9:
        return None
    out = n / d
    return out if math.isfinite(out) else None


def _ratio_value(ratios: FinancialRatios, name: str) -> float | None:
    rv = getattr(ratios, name, None)
    if rv is None:
        return None
    return rv.value if getattr(rv, "available", False) else None


def build_pd_features(
    financials: FinancialLineItems | None,
    ratios: FinancialRatios | None = None,
    prior: FinancialLineItems | None = None,
) -> PdFeatureVector:
    """Assemble the canonical PD feature vector from a borrower's financials.

    Reuses :func:`~app.analysis.ratios.compute_ratios` for the ratios it already
    defines (so the CAM, the scorecard, and the model all read one computation),
    and derives the remaining balance-sheet-structure features directly from the
    line items with the same formulas the training harmonizer uses. Missing
    inputs yield ``None`` (NaN-routed by the tree), never a fabricated value.

    ``prior`` is the immediately preceding fiscal period, used only for
    ``revenue_growth``; when the statements are single-period (the common case
    today) it is ``None`` and revenue_growth is left missing rather than guessed.
    """
    if financials is None:
        return PdFeatureVector()
    ratios = ratios or compute_ratios(financials)

    total_assets = financials.total_assets
    total_equity = financials.total_equity
    revenue = financials.revenue
    ebitda = financials.ebitda
    working_capital = None
    if financials.current_assets is not None and financials.current_liabilities is not None:
        working_capital = financials.current_assets - financials.current_liabilities
    # Total outside liabilities = total assets − total equity (the accounting
    # identity). This matches the training feature (Polish Attr2 =
    # total_liabilities/total_assets); IntelliCredit's ``total_debt`` is only
    # interest-bearing borrowings — a strict subset of liabilities — so using it
    # here would be a different quantity than the model was trained on.
    tol = None
    if total_assets is not None and total_equity is not None:
        tol = total_assets - total_equity

    # Revenue growth from the prior fiscal year, when a comparative is supplied.
    revenue_growth = None
    if prior is not None and prior.revenue is not None and revenue is not None:
        growth = _div(revenue, prior.revenue)
        revenue_growth = (growth - 1.0) if growth is not None else None

    return PdFeatureVector(
        roa=_div(financials.net_profit, total_assets),
        net_margin=_ratio_value(ratios, "net_margin"),
        ebitda_margin=_ratio_value(ratios, "ebitda_margin"),
        asset_turnover=_div(revenue, total_assets),
        current_ratio=_ratio_value(ratios, "current_ratio"),
        quick_ratio=_ratio_value(ratios, "quick_ratio"),
        liabilities_to_assets=_div(tol, total_assets),
        equity_to_assets=_div(total_equity, total_assets),
        # liabilities / equity (= tol_tnw), matching Polish Attr2/Attr10 — NOT
        # ratios.debt_to_equity, which is interest-bearing debt / equity.
        debt_to_equity=_ratio_value(ratios, "tol_tnw"),
        working_capital_to_assets=_div(working_capital, total_assets),
        ebitda_to_liabilities=_div(ebitda, tol),
        receivable_days=_ratio_value(ratios, "receivable_days"),
        inventory_days=_ratio_value(ratios, "inventory_days"),
        revenue_growth=revenue_growth,
    )


def feature_row(vec: PdFeatureVector) -> list[float]:
    """The ordered numeric row for the model; missing values become NaN."""
    row: list[float] = []
    for name in FEATURE_NAMES:
        value = getattr(vec, name)
        row.append(float("nan") if value is None else float(value))
    return row
