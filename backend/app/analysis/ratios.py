"""Deterministic financial ratio analysis (ARCHITECTURE §9 financial intelligence).

Every ratio is computed from extracted statement line items with explicit
divide-by-zero and missing-input guards: a ratio the data cannot support is
``None`` (never fabricated, never zero-filled). Each ratio carries a plain
explanation so the CAM can walk a reader through the arithmetic — explainability
is structural, not prompted (invariant #1).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from app.docintel.schemas import FinancialLineItems

_DAYS = Decimal(365)


def _ratio(numerator: Decimal | None, denominator: Decimal | None) -> float | None:
    """Safe division: None when either input is missing or the denominator is 0."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator / denominator)


class RatioValue(BaseModel):
    """One computed ratio with its inputs and a human explanation."""

    value: float | None = None
    explanation: str = ""
    available: bool = False


class FinancialRatios(BaseModel):
    """The canonical ratio set consumed by the scorecard and the CAM."""

    fiscal_year: str | None = None
    # Liquidity
    current_ratio: RatioValue = Field(default_factory=RatioValue)
    quick_ratio: RatioValue = Field(default_factory=RatioValue)
    # Leverage
    debt_to_equity: RatioValue = Field(default_factory=RatioValue)
    tol_tnw: RatioValue = Field(default_factory=RatioValue)
    debt_to_ebitda: RatioValue = Field(default_factory=RatioValue)
    # Coverage
    interest_coverage: RatioValue = Field(default_factory=RatioValue)
    dscr: RatioValue = Field(default_factory=RatioValue)
    # Profitability
    ebitda_margin: RatioValue = Field(default_factory=RatioValue)
    net_margin: RatioValue = Field(default_factory=RatioValue)
    # Efficiency
    receivable_days: RatioValue = Field(default_factory=RatioValue)
    inventory_days: RatioValue = Field(default_factory=RatioValue)
    # Cash-flow quality
    cfo_to_ebitda: RatioValue = Field(default_factory=RatioValue)

    def available_ratios(self) -> dict[str, float]:
        """Name → value for every ratio the data supported."""
        result: dict[str, float] = {}
        for name, field in self.__dict__.items():
            if isinstance(field, RatioValue) and field.available and field.value is not None:
                result[name] = field.value
        return result


def _rv(value: float | None, explanation: str) -> RatioValue:
    return RatioValue(value=value, explanation=explanation, available=value is not None)


def compute_ratios(fin: FinancialLineItems) -> FinancialRatios:
    """Compute the canonical ratio set from one fiscal year's line items."""
    ca, cl = fin.current_assets, fin.current_liabilities
    inv = fin.inventory
    quick_assets = (ca - inv) if (ca is not None and inv is not None) else None
    tol = (
        (fin.total_assets - fin.total_equity)
        if (fin.total_assets is not None and fin.total_equity is not None)
        else None
    )

    current = _ratio(ca, cl)
    quick = _ratio(quick_assets, cl)
    d_e = _ratio(fin.total_debt, fin.total_equity)
    tol_tnw = _ratio(tol, fin.total_equity)
    d_ebitda = _ratio(fin.total_debt, fin.ebitda)
    icr = _ratio(fin.ebitda, fin.interest_expense)
    # DSCR proxy: EBITDA over interest (principal schedule is not in the
    # statements — flagged approximate; the term-limit gate uses this).
    dscr = _ratio(fin.ebitda, fin.interest_expense)
    ebitda_m = _ratio(fin.ebitda, fin.revenue)
    net_m = _ratio(fin.net_profit, fin.revenue)
    rec_days = _ratio(fin.receivables * _DAYS if fin.receivables is not None else None, fin.revenue)
    inv_days = _ratio(inv * _DAYS if inv is not None else None, fin.revenue)
    cfo_ebitda = _ratio(fin.cash_flow_from_operations, fin.ebitda)

    return FinancialRatios(
        fiscal_year=fin.fiscal_year,
        current_ratio=_rv(current, "current assets / current liabilities"),
        quick_ratio=_rv(quick, "(current assets − inventory) / current liabilities"),
        debt_to_equity=_rv(d_e, "total debt / total equity"),
        tol_tnw=_rv(tol_tnw, "total outside liabilities / tangible net worth (approx.)"),
        debt_to_ebitda=_rv(d_ebitda, "total debt / EBITDA"),
        interest_coverage=_rv(icr, "EBITDA / interest expense"),
        dscr=_rv(dscr, "EBITDA / interest expense (DSCR proxy; excludes principal)"),
        ebitda_margin=_rv(ebitda_m, "EBITDA / revenue"),
        net_margin=_rv(net_m, "net profit / revenue"),
        receivable_days=_rv(rec_days, "receivables × 365 / revenue"),
        inventory_days=_rv(inv_days, "inventory × 365 / revenue"),
        cfo_to_ebitda=_rv(cfo_ebitda, "cash flow from operations / EBITDA"),
    )


# Piecewise-linear breakpoints (raw value → 0..100 health points). Sector-
# neutral defaults; a sector pack can override in Phase 6.
_HEALTH_BREAKPOINTS: dict[str, tuple[bool, list[tuple[float, float]]]] = {
    # (higher_is_better, [(raw, points), ...])
    "current_ratio": (True, [(0.8, 20), (1.0, 45), (1.33, 65), (2.0, 90)]),
    "interest_coverage": (True, [(1.0, 15), (1.5, 40), (3.0, 70), (6.0, 95)]),
    "debt_to_equity": (False, [(0.5, 95), (1.0, 75), (2.0, 45), (3.0, 20)]),
    "ebitda_margin": (True, [(0.03, 20), (0.08, 50), (0.15, 75), (0.25, 95)]),
    "cfo_to_ebitda": (True, [(0.2, 25), (0.5, 55), (0.8, 80), (1.0, 95)]),
}


def _interpolate(value: float, higher_better: bool, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear map of a raw ratio to 0..100, clamped at the ends."""
    xs = points if higher_better else list(reversed([(r, p) for r, p in points]))
    # Work in ascending raw order.
    ordered = sorted(points, key=lambda p: p[0])
    if value <= ordered[0][0]:
        return ordered[0][1]
    if value >= ordered[-1][0]:
        return ordered[-1][1]
    for (x0, y0), (x1, y1) in zip(ordered, ordered[1:], strict=False):
        if x0 <= value <= x1:
            frac = (value - x0) / (x1 - x0) if x1 != x0 else 0.0
            return y0 + frac * (y1 - y0)
    _ = xs
    return ordered[-1][1]


def financial_health_score(ratios: FinancialRatios) -> float | None:
    """A 0..100 composite health score over the available scored ratios.

    Returns None when no scorable ratio is available — an honest "insufficient
    data" rather than a fabricated score.
    """
    scores: list[float] = []
    available = ratios.available_ratios()
    for name, (higher_better, breakpoints) in _HEALTH_BREAKPOINTS.items():
        if name in available:
            scores.append(_interpolate(available[name], higher_better, breakpoints))
    if not scores:
        return None
    return round(sum(scores) / len(scores), 1)
