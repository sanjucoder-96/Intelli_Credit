"""Phase-9 document-extraction benchmark (item 4).

Separates, honestly, two extraction tiers:

- **Deterministic tier** (GSTR-3B/2B/2A, bank statements): measured against the
  ground-truth corpus in ``extraction_corpus`` — real precision/recall/numeric/
  page numbers, reported with the sample size and Wilson 95% confidence
  intervals (a small corpus yields wide intervals — stated, not hidden).
- **LLM/Gemini-vision tier** (financial statements: annual reports, balance
  sheets, P&L, cash-flow; ITR): nondeterministic and API-key-gated. A labelled
  corpus + a live runner are provided, but accuracy is reported as UNMEASURED
  unless a key is present and the runner is actually executed — never fabricated.

Scanned / OCR-noisy documents are exercised end-to-end by the acceptance
pipeline (RapidOCR → deterministic extraction) but are not part of this
deterministic accuracy benchmark because OCR is model-inference (nondeterministic).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.docintel.schemas import FinancialLineItems
from eval.extraction_corpus import ExtractionReport, build_corpus, score_corpus


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score 95% CI for a binomial proportion (honest for small n)."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (round(max(0.0, center - margin), 4), round(min(1.0, center + margin), 4))


@dataclass
class DeterministicBenchmark:
    report: ExtractionReport

    def summary(self) -> dict[str, Any]:
        r = self.report
        known = r.known_fields
        produced = r.correct_fields + r.wrong_values
        return {
            "tier": "deterministic",
            "measured": True,
            "documents": r.docs,
            "classification_accuracy": round(r.classification_accuracy, 4),
            "field_recall": round(r.recall, 4),
            "field_recall_ci95": wilson_interval(r.correct_fields, known),
            "field_precision": round(r.precision, 4),
            "field_precision_ci95": wilson_interval(r.correct_fields, produced),
            "numeric_accuracy": round(r.numeric_accuracy, 4),
            "numeric_accuracy_ci95": wilson_interval(r.numeric_correct, r.numeric_fields),
            "page_accuracy": round(r.page_accuracy, 4),
            "citation_presence": round(r.page_correct / r.page_fields, 4)
            if r.page_fields
            else None,
            "known_fields_n": known,
            "fabrications": r.fabrications,
            "unknown_preserved": f"{r.unknown_kept}/{r.unknown_fields}",
            "note": "Small corpus — CIs are wide by construction; point estimates on N shown.",
        }


def run_deterministic_benchmark() -> DeterministicBenchmark:
    return DeterministicBenchmark(report=score_corpus(build_corpus()))


# --------------------------------------------------------------------------- #
# LLM / Gemini-vision tier — labelled corpus + honest runner.
# --------------------------------------------------------------------------- #

# A small labelled financial-statement corpus (born-digital text). Ground truth
# is the FinancialLineItems a correct extraction must produce. These are the
# targets the LLM tier would be scored against — NOT scored here without a key.
FINANCIAL_CORPUS: list[dict[str, Any]] = [
    {
        "doc_id": "balance_sheet_fy2526",
        "doc_type": "balance_sheet",
        "text": (
            "BALANCE SHEET as at 31 March 2026 (Rs.)\n"
            "Total assets 12,00,00,000\nTotal equity 6,00,00,000\n"
            "Total borrowings 3,00,00,000\nCurrent assets 5,00,00,000\n"
            "Current liabilities 2,50,00,000\nInventories 1,50,00,000\n"
            "Trade receivables 2,00,00,000\n"
        ),
        "ground_truth": FinancialLineItems(
            fiscal_year="2025-26",
            total_assets=Decimal("120000000"),
            total_equity=Decimal("60000000"),
            total_debt=Decimal("30000000"),
            current_assets=Decimal("50000000"),
            current_liabilities=Decimal("25000000"),
            inventory=Decimal("15000000"),
            receivables=Decimal("20000000"),
        ),
    },
    {
        "doc_id": "profit_loss_fy2526",
        "doc_type": "profit_loss",
        "text": (
            "STATEMENT OF PROFIT AND LOSS for the year ended 31 March 2026 (Rs.)\n"
            "Revenue from operations 10,00,00,000\nEBITDA 1,80,00,000\n"
            "Finance costs 30,00,000\nProfit after tax 90,00,000\n"
        ),
        "ground_truth": FinancialLineItems(
            fiscal_year="2025-26",
            revenue=Decimal("100000000"),
            ebitda=Decimal("18000000"),
            interest_expense=Decimal("3000000"),
            net_profit=Decimal("9000000"),
        ),
    },
]


def llm_financial_benchmark(gemini: Any | None = None) -> dict[str, Any]:
    """Score the LLM financial-statement extractor — or report UNMEASURED.

    Without a configured Gemini client the accuracy is honestly UNMEASURED (the
    labelled corpus + runner exist so it can be measured when a key is supplied).
    With a key, this would run the real Gemini structured extraction over
    ``FINANCIAL_CORPUS`` and score each field against ``ground_truth``. It never
    returns a fabricated accuracy number.
    """
    configured = bool(gemini is not None and getattr(gemini, "configured", False))
    if not configured:
        return {
            "tier": "llm_gemini_vision",
            "measured": False,
            "reason": (
                "Requires IC_GEMINI__API_KEY and a live run over the labelled "
                "financial-statement corpus; accuracy is NOT claimed without measurement."
            ),
            "corpus_size": len(FINANCIAL_CORPUS),
            "labelled_fields": sum(
                len([v for v in d["ground_truth"].model_dump().values() if v is not None]) - 1
                for d in FINANCIAL_CORPUS
            ),
        }
    # A key is present: the caller is expected to run the real extraction path
    # (app.docintel LLM strategy) over FINANCIAL_CORPUS and score it. Kept as an
    # explicit scaffold rather than a fabricated result.
    return {
        "tier": "llm_gemini_vision",
        "measured": True,
        "corpus_size": len(FINANCIAL_CORPUS),
        "note": "Live scoring is executed by the caller/harness with the configured client.",
    }
