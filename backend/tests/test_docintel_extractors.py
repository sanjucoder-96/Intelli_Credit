"""Deterministic extractor unit tests (GSTR-3B, GSTR-2B, bank statements)."""

from __future__ import annotations

from decimal import Decimal

from app.docintel.extractors.bank_statement import (
    extract_bank_statement_csv,
    extract_bank_statement_rows,
)
from app.docintel.extractors.gstr2b import extract_gstr2b
from app.docintel.extractors.gstr3b import extract_gstr3b
from app.docintel.parsing import parse_amount, parse_date, parse_period

GSTR3B_TEXT = """FORM GSTR-3B
[See rule 61(5)]
GSTIN 27AAACA1234A1Z5
Legal name of the registered person ACME TEXTILES PRIVATE LIMITED
Period 04-2026
3.1 Details of Outward Supplies and inward supplies liable to reverse charge
(a) Outward taxable supplies (other than zero rated) 1,20,50,000.00
Integrated Tax 10,84,500.00
Central Tax 0.00
State/UT Tax 0.00
Cess 0.00
4. Eligible ITC
(A) ITC Available - All other ITC - IGST 6,20,000.00
Tax paid in cash 2,64,500.00
Tax paid through ITC 8,20,000.00
"""


def test_parse_amount_indian_formats() -> None:
    assert parse_amount("1,23,456.78") == Decimal("123456.78")
    assert parse_amount("₹ 1,000") == Decimal("1000")
    assert parse_amount("(2,500.00)") == Decimal("-2500.00")
    assert parse_amount("Rs. 42") == Decimal("42")
    assert parse_amount("NEFT-HDFC") is None
    assert parse_amount("") is None


def test_parse_date_formats() -> None:
    assert parse_date("01/04/2026") is not None
    assert parse_date("15-Apr-26") is not None
    assert parse_date("2026-04-30") is not None
    assert parse_date("not a date") is None


def test_parse_period() -> None:
    assert parse_period("Period 04-2026") == "2026-04"
    assert parse_period("nothing here") is None


def test_gstr3b_extraction_and_citations() -> None:
    result = extract_gstr3b([GSTR3B_TEXT])
    data = result.data
    assert data.gstin == "27AAACA1234A1Z5"  # type: ignore[union-attr]
    assert data.period == "2026-04"  # type: ignore[union-attr]
    assert data.outward_taxable_value == Decimal("12050000.00")  # type: ignore[union-attr]
    assert data.outward_tax.igst == Decimal("1084500.00")  # type: ignore[union-attr]
    assert data.itc_claimed.igst == Decimal("620000.00")  # type: ignore[union-attr]
    assert data.tax_paid_cash == Decimal("264500.00")  # type: ignore[union-attr]
    # Every extracted field carries a page + quote citation.
    assert result.citations["gstin"].page == 1
    assert "GSTIN" in (result.citations["gstin"].quote or "")
    assert result.citations["outward_taxable_value"].page == 1
    assert not [issue for issue in result.issues if issue.severity == "error"]
    assert result.field_confidence["outward_taxable_value"] >= 0.9


def test_gstr3b_missing_table_is_an_error_not_a_zero() -> None:
    result = extract_gstr3b(["FORM GSTR-3B\nGSTIN 27AAACA1234A1Z5\nnothing else"])
    assert result.data.outward_taxable_value is None  # type: ignore[union-attr]
    assert any(issue.severity == "error" for issue in result.issues)


def test_gstr2b_supplier_rows_and_totals() -> None:
    tables = [
        (
            2,
            [
                ["GSTIN of supplier", "Trade name", "Taxable value", "IGST", "CGST", "SGST"],
                ["29AACCN9012C1Z8", "Nimbus Logistics", "10,00,000", "1,80,000", "0", "0"],
                ["36AABCD5678B1Z3", "Deccan Agro", "5,00,000", "0", "45,000", "45,000"],
            ],
        )
    ]
    result = extract_gstr2b(["FORM GSTR-2B\nGSTIN 27AAACA1234A1Z5\nPeriod 04-2026"], tables)
    data = result.data
    assert len(data.suppliers) == 2  # type: ignore[union-attr]
    assert data.itc_available.total == Decimal("270000")  # type: ignore[union-attr]
    assert result.citations["suppliers[0]"].page == 2


def test_bank_statement_rows_aggregates_and_continuity() -> None:
    rows = [
        ["Account Statement", "", "", "", ""],
        ["A/C No: 001234567890  IFSC: HDFC0001234", "", "", "", ""],
        ["Date", "Narration", "Debit", "Credit", "Balance"],
        ["01/04/2026", "OPENING", "", "", "1,00,000.00"],
        ["05/04/2026", "NEFT-ACME PAYMENT", "", "5,00,000.00", "6,00,000.00"],
        ["10/04/2026", "CHQ RTN - INSUFF FUNDS", "2,000.00", "", "5,98,000.00"],
        ["03/05/2026", "UPI-VENDOR", "1,00,000.00", "", "4,98,000.00"],
    ]
    header = " ".join(cell for row in rows[:3] for cell in row)
    result = extract_bank_statement_rows(rows, header_text=header, cite="Sheet1")
    data = result.data
    assert data.account_number_masked == "XXXX7890"  # type: ignore[union-attr]  # PII masked
    assert data.ifsc == "HDFC0001234"  # type: ignore[union-attr]
    assert data.balance_continuity_ok is True  # type: ignore[union-attr]
    assert len(data.transactions) == 4  # type: ignore[union-attr]
    april, may = data.monthly  # type: ignore[union-attr]
    assert april.period == "2026-04"
    assert april.credits == Decimal("500000.00")
    assert april.cheque_returns == 1
    assert may.debits == Decimal("100000.00")
    assert result.field_confidence["transactions"] >= 0.9


def test_bank_statement_broken_continuity_lowers_confidence() -> None:
    rows = [
        ["Date", "Narration", "Debit", "Credit", "Balance"],
        ["01/04/2026", "OPENING", "", "", "1,000.00"],
        ["02/04/2026", "NEFT IN", "", "500.00", "9,999.00"],  # inconsistent
    ]
    result = extract_bank_statement_rows(rows)
    assert result.data.balance_continuity_ok is False  # type: ignore[union-attr]
    assert result.field_confidence["transactions"] < 0.6
    assert any("continuity" in issue.message for issue in result.issues)


def test_bank_statement_csv_roundtrip() -> None:
    csv_bytes = (
        b"Account Statement,,,\n"
        b"A/c No: 555566667777 IFSC: ICIC0000042,,,\n"
        b"Date,Particulars,Withdrawal,Deposit,Balance\n"
        b"01/04/2026,OPENING,,,50000.00\n"
        b"02/04/2026,IMPS-CUSTOMER,,25000.00,75000.00\n"
    )
    result = extract_bank_statement_csv(csv_bytes)
    assert result.data.account_number_masked == "XXXX7777"  # type: ignore[union-attr]
    assert len(result.data.transactions) == 2  # type: ignore[union-attr]
