"""Phase-8 Indian-context evaluation (priority #7).

Tests the India-specific credit concepts the architecture ACTUALLY implements,
and pins the honest support boundary so an untested claim can't creep in:

- GST return family (GSTR-1/2A/2B/3B) + ITR + CIBIL Commercial + legal notice +
  sanction letter are recognised document types;
- GST↔bank-flow and GSTR-2B↔3B ITC reconciliation validators fire (the core
  Indian-lending fraud/consistency checks);
- Indian number formatting (lakh/crore grouping, ₹ / Rs.) parses correctly;
- Indian financial-year period parsing (YYYY-MM, April start).

Honest gaps (asserted, not hidden): GSTR-1, CIBIL Commercial, legal notices,
sanction letters and annual reports are CLASSIFIED but have no deterministic
extractor (extraction is LLM or none); MCA / e-Courts live retrieval is
CAPTCHA-gated per the architecture and is not a deterministic capability here.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.analysis.cam import CamReport
from app.docintel.parsing import parse_amount, parse_period
from app.docintel.registry import ExtractionStrategy, document_type_registry
from app.docintel.schemas import (
    BankStatementExtract,
    Gstr2bExtract,
    Gstr2bSupplierRow,
    Gstr3bExtract,
    MonthlyAggregates,
    TaxSplit,
)
from app.docintel.validators import (
    CaseExtracts,
    validate_gst_vs_bank_credits,
    validate_itc_mismatch,
)
from app.domain.enums import RiskCategory, Severity

# --------------------------------------------------------------------------- #
# Recognised Indian document taxonomy.
# --------------------------------------------------------------------------- #


def test_indian_gst_and_credit_document_types_are_recognised() -> None:
    types = set(document_type_registry.types())
    for dt in (
        "gstr1",
        "gstr2a",
        "gstr2b",
        "gstr3b",
        "itr",
        "cibil_commercial",
        "legal_notice",
        "sanction_letter",
        "annual_report",
    ):
        assert dt in types, f"{dt} missing from the document taxonomy"


def test_extraction_support_boundary_is_honest() -> None:
    """Pins which Indian doc types have a DETERMINISTIC extractor vs not."""

    def strategy(dt: str) -> ExtractionStrategy:
        return document_type_registry.get(dt).extraction

    # Deterministically extracted (GST forms + bank statements).
    for dt in ("gstr3b", "gstr2b", "gstr2a", "bank_statement"):
        assert strategy(dt) is ExtractionStrategy.DETERMINISTIC
    # LLM-extracted (financial statements / ITR) — nondeterministic, key-gated.
    for dt in ("itr", "balance_sheet", "profit_loss"):
        assert strategy(dt) is ExtractionStrategy.LLM
    # Classification-only today (documented gap — recognised, not yet extracted).
    for dt in ("gstr1", "cibil_commercial", "legal_notice", "sanction_letter", "annual_report"):
        assert strategy(dt) is ExtractionStrategy.NONE


def test_gst_forms_classify_by_statutory_anchor() -> None:
    assert document_type_registry.classify_tier1("FORM GSTR-3B [See rule 61(5)]") == (
        "gstr3b",
        0.95,
    )
    assert document_type_registry.classify_tier1("FORM GSTR-2B\nGSTIN 27AAACA1234A1Z5") == (
        "gstr2b",
        0.95,
    )


# --------------------------------------------------------------------------- #
# Indian GST reconciliation (the core lending consistency checks).
# --------------------------------------------------------------------------- #


def test_gst_vs_bank_credit_reconciliation_fires() -> None:
    extracts = CaseExtracts(
        gstr3b=[
            (
                uuid4(),
                Gstr3bExtract(
                    gstin="27AAACA1234A1Z5",
                    period="2026-04",
                    outward_taxable_value=Decimal("10000000"),
                    itc_claimed=TaxSplit(),
                ),
            )
        ],
        bank_statements=[
            (
                uuid4(),
                BankStatementExtract(
                    monthly=[MonthlyAggregates(period="2026-04", credits=Decimal("6000000"))]
                ),
            )
        ],
    )
    outcomes = validate_gst_vs_bank_credits(extracts)
    assert outcomes and outcomes[0].severity is Severity.HIGH  # 40% gap
    assert outcomes[0].category is RiskCategory.FINANCIAL


def test_gstr2b_vs_3b_itc_mismatch_fires_critical_above_20pct() -> None:
    extracts = CaseExtracts(
        gstr3b=[
            (
                uuid4(),
                Gstr3bExtract(
                    gstin="27AAACA1234A1Z5",
                    period="2026-04",
                    outward_taxable_value=Decimal("10000000"),
                    itc_claimed=TaxSplit(igst=Decimal("500000")),
                ),
            )
        ],
        gstr2b=[
            (
                uuid4(),
                Gstr2bExtract(
                    gstin="27AAACA1234A1Z5",
                    period="2026-04",
                    suppliers=[Gstr2bSupplierRow(itc=TaxSplit(igst=Decimal("300000")))],
                    itc_available=TaxSplit(igst=Decimal("300000")),
                ),
            )
        ],
    )
    outcomes = validate_itc_mismatch(extracts)
    assert outcomes  # 5,00,000 claimed vs 3,00,000 available → 66% excess
    assert outcomes[0].severity is Severity.CRITICAL
    assert outcomes[0].category is RiskCategory.LEGAL


# --------------------------------------------------------------------------- #
# Indian number + fiscal-year conventions.
# --------------------------------------------------------------------------- #


def test_indian_lakh_crore_grouping_parses() -> None:
    assert parse_amount("1,00,00,000") == Decimal("10000000")  # 1 crore
    assert parse_amount("1,23,456.78") == Decimal("123456.78")  # lakh grouping
    assert parse_amount("Rs. 42,00,000") == Decimal("4200000")
    assert parse_amount("₹ 1,000") == Decimal("1000")
    assert parse_amount("(2,50,000)") == Decimal("-250000")  # accounting negative


def test_indian_financial_year_period_parses_april_start() -> None:
    # Indian FY 2026-27 starts in April; GST periods are month-of-year.
    assert parse_period("Period 04-2026") == "2026-04"
    assert parse_period("Tax Period: 03-2027") == "2027-03"  # FY 2026-27 close


def test_cam_defaults_to_inr_currency() -> None:
    # The CAM currency defaults to INR (Indian rupees) for every memo.
    assert CamReport.model_fields["currency"].default == "INR"
