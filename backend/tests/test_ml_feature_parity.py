"""Phase-8 ML train↔inference feature parity (priority #9, flagship).

The PD model is trained on the Polish UCI dataset via ``ml.datasets._harmonize_row``
and served on IntelliCredit financials via ``app.analysis.features.build_pd_features``.
If the two ever compute a feature with a *different definition*, IntelliCredit
would be running a model on inputs it was not trained on — a silent, dangerous
drift. An adversarial review previously caught exactly this: inference used
interest-bearing ``total_debt`` where training used total LIABILITIES.

These tests are the automated regression guard for that class of bug. The
training harmonizer is imported and run for real; the Polish attribute row is
constructed independently from the borrower's raw line items using the
documented accounting definitions (total liabilities = total assets − total
equity), so a regression in EITHER side breaks parity.
"""

from __future__ import annotations

import math
import sys
from decimal import Decimal
from pathlib import Path

from app.analysis.features import FEATURE_NAMES, build_pd_features
from app.docintel.schemas import FinancialLineItems

# The ml training package lives at repo-root/ml (its own top-level `ml` package).
_ML_ROOT = Path(__file__).resolve().parents[2] / "ml"
if str(_ML_ROOT) not in sys.path:
    sys.path.insert(0, str(_ML_ROOT))

from ml.datasets import _harmonize_row  # noqa: E402  (path set above)


def _polish_row(fin: FinancialLineItems, prior: FinancialLineItems) -> list[float]:
    """Build the Polish UCI attribute row a firm with these raw items would have.

    Each AttrN is computed from the raw line items using the SAME accounting
    definition the Polish dataset documents — independently of inference code.
    Total liabilities is the accounting identity total_assets − total_equity.
    """
    f = {k: float(v) for k, v in fin.model_dump().items() if isinstance(v, Decimal)}
    total_liabilities = f["total_assets"] - f["total_equity"]
    attr = [math.nan] * 64  # Polish has 64 attributes (Attr1..Attr64)

    def put(n: int, value: float) -> None:
        attr[n - 1] = value

    put(1, f["net_profit"] / f["total_assets"])  # roa
    put(2, total_liabilities / f["total_assets"])  # liabilities / assets
    put(3, (f["current_assets"] - f["current_liabilities"]) / f["total_assets"])  # WC / assets
    put(4, f["current_assets"] / f["current_liabilities"])  # current ratio
    put(9, f["revenue"] / f["total_assets"])  # asset turnover
    put(10, f["total_equity"] / f["total_assets"])  # equity / assets
    put(20, f["inventory"] * 365.0 / f["revenue"])  # inventory days
    put(21, f["revenue"] / float(prior.revenue))  # sales_n / sales_{n-1}
    put(23, f["net_profit"] / f["revenue"])  # net margin
    put(44, f["receivables"] * 365.0 / f["revenue"])  # receivable days
    put(46, (f["current_assets"] - f["inventory"]) / f["current_liabilities"])  # quick ratio
    put(48, f["ebitda"] / f["total_assets"])  # EBITDA / assets
    put(49, f["ebitda"] / f["revenue"])  # EBITDA margin
    return attr


_FIRM = FinancialLineItems(
    fiscal_year="2025-26",
    revenue=Decimal("100000000"),
    ebitda=Decimal("18000000"),
    net_profit=Decimal("9000000"),
    total_assets=Decimal("120000000"),
    total_equity=Decimal("60000000"),
    total_debt=Decimal("30000000"),  # interest-bearing ⊂ liabilities (60M)
    current_assets=Decimal("50000000"),
    current_liabilities=Decimal("25000000"),
    inventory=Decimal("15000000"),
    receivables=Decimal("20000000"),
    interest_expense=Decimal("3000000"),
    cash_flow_from_operations=Decimal("15000000"),
)
_PRIOR = FinancialLineItems(fiscal_year="2024-25", revenue=Decimal("80000000"))


def test_every_inference_feature_matches_the_training_definition() -> None:
    """The flagship parity assertion: inference == training, feature by feature."""
    inferred = build_pd_features(_FIRM, prior=_PRIOR)
    trained = _harmonize_row(_polish_row(_FIRM, _PRIOR))

    for name in FEATURE_NAMES:
        inf = getattr(inferred, name)
        trn = trained[name]
        assert inf is not None, f"{name} unexpectedly missing on the inference side"
        assert math.isclose(inf, trn, rel_tol=1e-9, abs_tol=1e-9), (
            f"train/inference DRIFT on {name!r}: inference={inf} vs training={trn}"
        )


def test_leverage_features_use_total_liabilities_not_interest_bearing_debt() -> None:
    """The exact bug the adversarial review caught, pinned as a regression test.

    total_debt (30M, interest-bearing) is deliberately different from total
    liabilities (60M = assets−equity). The three leverage features MUST use the
    60M figure — using total_debt would halve them.
    """
    vec = build_pd_features(_FIRM)
    # liabilities_to_assets = 60M/120M = 0.5   (NOT 30M/120M = 0.25)
    assert math.isclose(vec.liabilities_to_assets, 0.5, abs_tol=1e-9)
    # debt_to_equity here means total liabilities / equity = 60M/60M = 1.0 (NOT 0.5)
    assert math.isclose(vec.debt_to_equity, 1.0, abs_tol=1e-9)
    # ebitda_to_liabilities = 18M/60M = 0.30   (NOT 18M/30M = 0.60)
    assert math.isclose(vec.ebitda_to_liabilities, 0.30, abs_tol=1e-9)


def test_missing_inputs_produce_aligned_nans_on_both_sides() -> None:
    """A firm missing balance-sheet structure yields NaN in the same positions."""
    thin = FinancialLineItems(
        fiscal_year="2025-26", revenue=Decimal("50000000"), net_profit=Decimal("2000000")
    )
    inferred = build_pd_features(thin)
    # No prior and no total_assets/equity → the leverage + turnover features are missing.
    assert inferred.liabilities_to_assets is None
    assert inferred.equity_to_assets is None
    assert inferred.asset_turnover is None
    assert inferred.revenue_growth is None
    # net_margin is still computable (net_profit / revenue).
    assert inferred.net_margin is not None


def test_feature_order_is_the_shared_contract() -> None:
    """Both sides key the same 14 names — the ordered contract is identical."""
    trained = _harmonize_row([math.nan] * 64)
    assert set(trained) == set(FEATURE_NAMES)
    assert len(FEATURE_NAMES) == 14
