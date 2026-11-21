"""Cross-document validator unit tests — pure math over typed extracts."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.docintel.registry import document_type_registry
from app.docintel.schemas import (
    BankStatementExtract,
    BankTxn,
    Gstr2bExtract,
    Gstr3bExtract,
    ItrExtract,
    MonthlyAggregates,
    TaxSplit,
)
from app.docintel.validators import (
    CaseExtracts,
    run_all,
    validate_cheque_returns,
    validate_gst_vs_bank_credits,
    validate_itc_mismatch,
)
from app.domain.enums import RiskCategory, Severity


def _gstr3b(period: str, outward: str, itc_igst: str = "0") -> Gstr3bExtract:
    return Gstr3bExtract(
        gstin="27AAACA1234A1Z5",
        period=period,
        outward_taxable_value=Decimal(outward),
        itc_claimed=TaxSplit(igst=Decimal(itc_igst)),
    )


def _bank(months: list[tuple[str, str, int]]) -> BankStatementExtract:
    return BankStatementExtract(
        monthly=[
            MonthlyAggregates(period=p, credits=Decimal(c), cheque_returns=r) for p, c, r in months
        ]
    )


def test_gst_vs_bank_flags_material_divergence_with_both_citations() -> None:
    doc_gst, doc_bank = uuid4(), uuid4()
    extracts = CaseExtracts(
        gstr3b=[(doc_gst, _gstr3b("2026-04", "10000000"))],
        bank_statements=[(doc_bank, _bank([("2026-04", "6000000", 0)]))],
    )
    outcomes = validate_gst_vs_bank_credits(extracts)
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome.severity is Severity.HIGH  # 40% gap
    assert outcome.category is RiskCategory.FINANCIAL
    cited_docs = {doc_id for doc_id, _ in outcome.citations}
    assert cited_docs == {doc_gst, doc_bank}  # evidence into BOTH documents


def test_gst_vs_bank_tolerates_small_gap() -> None:
    extracts = CaseExtracts(
        gstr3b=[(uuid4(), _gstr3b("2026-04", "10000000"))],
        bank_statements=[(uuid4(), _bank([("2026-04", "9200000", 0)]))],
    )
    assert validate_gst_vs_bank_credits(extracts) == []


def test_itc_mismatch_critical_above_20_percent() -> None:
    doc3b, doc2b = uuid4(), uuid4()
    extracts = CaseExtracts(
        gstr3b=[(doc3b, _gstr3b("2026-04", "1000000", itc_igst="500000"))],
        gstr2b=[
            (
                doc2b,
                Gstr2bExtract(period="2026-04", itc_available=TaxSplit(igst=Decimal("300000"))),
            )
        ],
    )
    outcomes = validate_itc_mismatch(extracts)
    assert len(outcomes) == 1
    assert outcomes[0].severity is Severity.CRITICAL  # 40% excess claim
    assert {doc_id for doc_id, _ in outcomes[0].citations} == {doc3b, doc2b}


def test_itc_mismatch_ignores_timing_noise() -> None:
    extracts = CaseExtracts(
        gstr3b=[(uuid4(), _gstr3b("2026-04", "1000000", itc_igst="102000"))],
        gstr2b=[
            (
                uuid4(),
                Gstr2bExtract(period="2026-04", itc_available=TaxSplit(igst=Decimal("100000"))),
            )
        ],
    )
    assert validate_itc_mismatch(extracts) == []


def test_cheque_returns_severity_scales() -> None:
    heavy = validate_cheque_returns(
        CaseExtracts(bank_statements=[(uuid4(), _bank([("2026-04", "0", 3)]))])
    )
    assert heavy[0].severity is Severity.HIGH
    light = validate_cheque_returns(
        CaseExtracts(
            bank_statements=[
                (uuid4(), _bank([("2026-01", "0", 1), ("2026-02", "0", 0), ("2026-03", "0", 0)]))
            ]
        )
    )
    assert light[0].severity is Severity.MEDIUM


def test_run_all_skips_missing_inputs_gracefully() -> None:
    assert run_all(CaseExtracts()) == []
    only_itr = CaseExtracts(itr=[(uuid4(), ItrExtract(business_turnover=Decimal("1")))])
    assert run_all(only_itr) == []


def test_bank_txn_dates_are_dates() -> None:
    txn = BankTxn(txn_date=date(2026, 4, 1), narration="X")
    assert txn.txn_date.year == 2026


def test_tier1_classification_anchors() -> None:
    assert document_type_registry.classify_tier1("FORM GSTR-3B [See rule 61(5)]") == (
        "gstr3b",
        0.95,
    )
    assert document_type_registry.classify_tier1("Statement of Account for ...")[0] == (
        "bank_statement"
    )
    assert document_type_registry.classify_tier1("random prose") is None
    assert "cibil_commercial" in document_type_registry.types()
