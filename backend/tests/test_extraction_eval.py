"""Phase-8 extraction evaluation (priority #2).

Runs the deterministic extraction corpus (``eval.extraction_corpus``) against
the real extractors and asserts honest aggregate quality plus the critical
adversarial invariant: a missing/ambiguous/garbled value is reported as UNKNOWN,
never fabricated. Deterministic — no services, no Gemini.
"""

from __future__ import annotations

from decimal import Decimal

from app.docintel.extractors.bank_statement import extract_bank_statement_rows
from app.docintel.extractors.gstr2b import extract_gstr2b
from eval.extraction_corpus import (
    _2B_HEAD,
    _2B_TABLES,
    _BANK_BROKEN_ROWS,
    build_corpus,
    score_corpus,
)


def test_corpus_extraction_quality_meets_thresholds() -> None:
    report = score_corpus(build_corpus())

    # Classification of the anchored, deterministic doc types is exact.
    assert report.classification_accuracy == 1.0

    # On the clean/known fields the deterministic extractors are exact.
    assert report.recall == 1.0, report.summary()
    assert report.precision == 1.0, report.summary()
    assert report.numeric_accuracy == 1.0, report.summary()
    assert report.page_accuracy == 1.0, report.summary()

    # THE honesty invariant: zero fabricated values across all adversarial docs.
    assert report.unknown_fields >= 2
    assert report.fabrications == 0, [o for o in report.outcomes if o.kind == "FABRICATED"]
    assert report.unknown_kept == report.unknown_fields


def test_misleading_prose_does_not_capture_the_decoy_figure() -> None:
    # Explicit regression: the 9,99,99,999 decoy in prose must never be picked.
    doc = next(d for d in build_corpus() if d.doc_id == "gstr3b_misleading")
    result = doc.run()
    assert result.data.outward_taxable_value == Decimal("9500000.00")


def test_broken_bank_continuity_lowers_confidence_not_certainty() -> None:
    result = extract_bank_statement_rows(_BANK_BROKEN_ROWS)
    assert result.data.balance_continuity_ok is False
    assert result.field_confidence["transactions"] < 0.6
    assert any("continuity" in issue.message for issue in result.issues)


def test_gstr2b_supplier_table_totals_are_exact() -> None:
    result = extract_gstr2b([_2B_HEAD], _2B_TABLES)
    assert len(result.data.suppliers) == 2
    # 1,80,000 (IGST) + 45,000 + 45,000 (CGST+SGST) = 2,70,000
    assert result.data.itc_available.total == Decimal("270000")


def test_report_summary_is_serializable_for_the_acceptance_report() -> None:
    summary = score_corpus(build_corpus()).summary()
    assert set(summary) >= {
        "classification_accuracy",
        "field_precision",
        "field_recall",
        "numeric_accuracy",
        "page_accuracy",
        "fabrications",
    }
