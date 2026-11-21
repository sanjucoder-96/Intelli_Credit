"""Dataset loading + harmonization onto the canonical PD feature schema.

The PD model trains on **real public bankruptcy data only** (ADR-0010). The
primary corpus here is the Polish Companies Bankruptcy set (UCI #365): ~43k
firm-year observations across five forecast horizons, each row 64 documented
financial ratios plus a bankruptcy label. Those raw ratios are mapped onto the
exact ``FEATURE_NAMES`` the backend computes at inference
(:mod:`app.analysis.features`) with the same formulas, so training and inference
share one feature space. Foreign firms are not Indian corporates — per ADR-0010
the resulting signal is a *relative* default-risk factor, never an absolute PD.

Synthetic demo data (``data/synthetic/``) is **never** loaded here; every row
this module emits carries ``data_origin='real'`` and a content hash so the
registry (and CI) can prove no synthetic data reached training.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from app.analysis.features import FEATURE_NAMES

# Polish attribute definitions used (1-indexed AttrN, documented by Zieba et al.
# 2016 / UCI #365). Column index in the ARFF is AttrN-1; the label is the last
# column. Only the attributes that map cleanly onto our canonical features are
# referenced; the rest are intentionally dropped (ADR-0010: harmonization is
# lossy by design).
_A = lambda n: n - 1  # noqa: E731 — AttrN (1-indexed) -> 0-indexed column


def _safe_div(a: float, b: float) -> float:
    if not math.isfinite(a) or not math.isfinite(b) or abs(b) < 1e-9:
        return math.nan
    out = a / b
    return out if math.isfinite(out) else math.nan


# Each canonical feature -> a function of the Polish attribute row (a dict
# {attr_index: value}). Formulas mirror app.analysis.features.build_pd_features
# so the training feature and the inference feature are the same quantity.
def _harmonize_row(attr: list[float]) -> dict[str, float]:
    def a(n: int) -> float:
        v = attr[_A(n)]
        return v if math.isfinite(v) else math.nan

    liab_assets = a(2)  # total liabilities / total assets
    equity_assets = a(10)  # equity / total assets
    ebitda_assets = a(48)  # EBITDA / total assets
    sales_ratio = a(21)  # sales_n / sales_{n-1}
    growth = sales_ratio - 1.0 if math.isfinite(sales_ratio) else math.nan
    return {
        "roa": a(1),  # net profit / total assets
        "net_margin": a(23),  # net profit / sales
        "ebitda_margin": a(49),  # EBITDA / sales
        "asset_turnover": a(9),  # sales / total assets
        "current_ratio": a(4),  # current assets / short-term liabilities
        "quick_ratio": a(46),  # (current assets - inventory) / short-term liab
        "liabilities_to_assets": liab_assets,
        "equity_to_assets": equity_assets,
        "debt_to_equity": _safe_div(liab_assets, equity_assets),  # liab/equity
        "working_capital_to_assets": a(3),  # working capital / total assets
        "ebitda_to_liabilities": _safe_div(ebitda_assets, liab_assets),  # EBITDA/liab
        "receivable_days": a(44),  # (receivables * 365) / sales
        "inventory_days": a(20),  # (inventory * 365) / sales
        "revenue_growth": growth,
    }


@dataclass
class Dataset:
    """A harmonized training dataset: features in FEATURE_NAMES order + labels."""

    name: str
    X: np.ndarray  # shape (n, len(FEATURE_NAMES)), NaN for missing
    y: np.ndarray  # shape (n,), {0,1}
    feature_names: tuple[str, ...]
    source_files: list[str]
    sha256: str
    data_origin: str = "real"

    @property
    def positive_rate(self) -> float:
        return float(self.y.mean()) if self.y.size else 0.0

    def lineage(self) -> dict[str, object]:
        return {
            "dataset": self.name,
            "source_files": self.source_files,
            "sha256": self.sha256,
            "n_rows": int(self.X.shape[0]),
            "n_features": int(self.X.shape[1]),
            "positive_rate": round(self.positive_rate, 6),
            "data_origin": self.data_origin,
        }


def _parse_arff(path: Path) -> list[list[float]]:
    """Minimal ARFF reader: numeric attributes + '?' missing, returns raw rows."""
    rows: list[list[float]] = []
    in_data = False
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("%"):
                continue
            low = s.lower()
            if low.startswith("@data"):
                in_data = True
                continue
            if not in_data or low.startswith("@"):
                continue
            cells = s.split(",")
            parsed: list[float] = []
            for c in cells:
                c = c.strip()
                parsed.append(math.nan if c == "?" else float(c))
            rows.append(parsed)
    return rows


def load_polish(data_dir: Path, horizons: tuple[int, ...] = (1, 2, 3, 4, 5)) -> Dataset:
    """Load + harmonize the Polish bankruptcy ARFF files into one Dataset.

    Pools the requested forecast horizons (1year..5year). Each row is mapped to
    the canonical feature vector; the label is the final ARFF column.
    """
    files: list[Path] = []
    for h in horizons:
        p = data_dir / f"{h}year.arff"
        if p.exists():
            files.append(p)
    if not files:
        raise FileNotFoundError(
            f"No Polish ARFF files under {data_dir}. Download UCI #365 into data/real/polish/ "
            "(see data/real/README.md)."
        )

    feature_rows: list[list[float]] = []
    labels: list[int] = []
    hasher = hashlib.sha256()
    for path in sorted(files):
        hasher.update(path.read_bytes())
        for raw in _parse_arff(path):
            attrs, label = raw[:-1], raw[-1]
            if not math.isfinite(label):
                continue
            feats = _harmonize_row(attrs)
            feature_rows.append([feats[name] for name in FEATURE_NAMES])
            labels.append(int(label))

    X = np.asarray(feature_rows, dtype=np.float64)
    y = np.asarray(labels, dtype=np.int64)
    return Dataset(
        name="polish_uci_365",
        X=X,
        y=y,
        feature_names=FEATURE_NAMES,
        source_files=[p.name for p in sorted(files)],
        sha256=hasher.hexdigest(),
    )
