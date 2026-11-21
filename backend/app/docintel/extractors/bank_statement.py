"""Deterministic bank-statement extractor (XLSX/CSV native; PDF table rows).

Header synonyms cover the major Indian banks' export formats. Account
numbers are masked at extraction (§15.6 PII rule) — the full number never
reaches the database or an LLM prompt. Confidence is computed from the
running-balance continuity check: if every row's balance equals the previous
balance plus credit minus debit (within rounding), the rows are trustworthy.
"""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal

from app.docintel.citations import Citation, FieldIssue
from app.docintel.parsing import parse_amount, parse_date
from app.docintel.schemas import (
    BankStatementExtract,
    BankTxn,
    ExtractionResult,
    MonthlyAggregates,
)

SCHEMA_ID = "bank_statement"
SCHEMA_VERSION = "1"

_HEADER_SYNONYMS: dict[str, tuple[str, ...]] = {
    "date": ("date", "txn date", "transaction date", "value date", "tran date"),
    "narration": ("narration", "particulars", "description", "remarks", "transaction details"),
    "debit": ("debit", "withdrawal", "withdrawal amt", "dr", "debit amount", "withdrawals"),
    "credit": ("credit", "deposit", "deposit amt", "cr", "credit amount", "deposits"),
    "balance": ("balance", "closing balance", "running balance", "balance amt"),
}

_CHANNELS = (
    ("NEFT", "NEFT"),
    ("RTGS", "RTGS"),
    ("IMPS", "IMPS"),
    ("UPI", "UPI"),
    ("CHQ", "CHEQUE"),
    ("CHEQUE", "CHEQUE"),
    ("CASH", "CASH"),
    ("CHARGE", "CHARGES"),
    ("CHRG", "CHARGES"),
)

_RETURN_MARKERS = ("RETURN", "RTN", "BOUNCE", "DISHONOUR", "DISHONOR", "INSUFF")
_ACCOUNT_PATTERN = re.compile(r"(?:a/?c|account)\D{0,10}(\d{9,18})", re.IGNORECASE)
_IFSC_PATTERN = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")


def _classify_channel(narration: str) -> str:
    upper = narration.upper()
    for marker, channel in _CHANNELS:
        if marker in upper:
            return channel
    return "OTHER"


def _is_cheque_return(narration: str) -> bool:
    upper = narration.upper()
    return any(marker in upper for marker in _RETURN_MARKERS)


def _map_header(cells: list[str]) -> dict[str, int] | None:
    """Map a candidate header row to column indexes; None if not a header."""
    mapping: dict[str, int] = {}
    for index, cell in enumerate(cells):
        label = (cell or "").strip().lower()
        if not label:
            continue
        for field, synonyms in _HEADER_SYNONYMS.items():
            if field not in mapping and any(label.startswith(s) for s in synonyms):
                mapping[field] = index
                break
    required = {"date", "narration", "balance"}
    return mapping if required <= set(mapping) else None


def extract_bank_statement_rows(
    rows: list[list[str]],
    *,
    header_text: str = "",
    cite: str | None = None,
) -> ExtractionResult:
    """Extract from raw cell rows (CSV/XLSX/PDF-table all normalize to this).

    ``header_text`` is any leading free text (account/IFSC live there);
    ``cite`` labels citations (sheet name or "table").
    """
    citations: dict[str, Citation] = {}
    confidence: dict[str, float] = {}
    issues: list[FieldIssue] = []

    account_masked = None
    account_match = _ACCOUNT_PATTERN.search(header_text)
    if account_match:
        number = account_match.group(1)
        account_masked = f"XXXX{number[-4:]}"  # PII: mask before persistence
        confidence["account_number_masked"] = 0.9
    ifsc_match = _IFSC_PATTERN.search(header_text)
    ifsc = ifsc_match.group(0) if ifsc_match else None

    header_map: dict[str, int] | None = None
    header_row_index = -1
    for index, row in enumerate(rows[:20]):
        header_map = _map_header([str(cell) if cell is not None else "" for cell in row])
        if header_map:
            header_row_index = index
            break
    if header_map is None:
        issues.append(
            FieldIssue(
                field_path="transactions",
                message="No recognizable header row (date/narration/balance)",
                severity="error",
            )
        )
        return ExtractionResult(
            schema_id=SCHEMA_ID,
            schema_version=SCHEMA_VERSION,
            data=BankStatementExtract(account_number_masked=account_masked, ifsc=ifsc),
            field_confidence=confidence,
            citations=citations,
            issues=issues,
        )

    transactions: list[BankTxn] = []
    for row_number, row in enumerate(rows[header_row_index + 1 :], start=header_row_index + 2):
        cells = [str(cell) if cell is not None else "" for cell in row]

        def cell(field: str, cells: list[str] = cells) -> str:
            index = header_map.get(field)
            return cells[index] if index is not None and index < len(cells) else ""

        txn_date = parse_date(cell("date"))
        if txn_date is None:
            continue  # trailer/summary rows
        narration = cell("narration").strip()
        transactions.append(
            BankTxn(
                txn_date=txn_date,
                narration=narration,
                debit=parse_amount(cell("debit")),
                credit=parse_amount(cell("credit")),
                balance=parse_amount(cell("balance")),
                channel=_classify_channel(narration),
            )
        )
        if len(transactions) == 1 and cite:
            citations["transactions"] = Citation(sheet=cite, cell=f"row {row_number}")

    continuity_ok = _check_balance_continuity(transactions)
    monthly = _monthly_aggregates(transactions)

    row_confidence = 0.9 if continuity_ok else 0.55
    if transactions:
        confidence["transactions"] = row_confidence
        confidence["monthly"] = row_confidence
    if continuity_ok is False:
        issues.append(
            FieldIssue(
                field_path="transactions",
                message="Running-balance continuity failed — rows may be missing or misparsed",
            )
        )

    data = BankStatementExtract(
        account_number_masked=account_masked,
        ifsc=ifsc,
        period_start=min((t.txn_date for t in transactions), default=None),
        period_end=max((t.txn_date for t in transactions), default=None),
        transactions=transactions,
        monthly=monthly,
        balance_continuity_ok=continuity_ok,
    )
    return ExtractionResult(
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        data=data,
        field_confidence=confidence,
        citations=citations,
        issues=issues,
    )


def extract_bank_statement_xlsx(content: bytes) -> ExtractionResult:
    """Extract from an XLSX workbook (first sheet with a recognizable header)."""
    from openpyxl import load_workbook

    workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            rows = [
                ["" if cell is None else str(cell) for cell in row]
                for row in sheet.iter_rows(values_only=True)
            ]
            header_text = " ".join(cell for row in rows[:8] for cell in row if cell)
            result = extract_bank_statement_rows(rows, header_text=header_text, cite=sheet.title)
            if not any(issue.severity == "error" for issue in result.issues):
                return result
        return result  # last sheet's (failed) result carries the error issue
    finally:
        workbook.close()


def extract_bank_statement_csv(content: bytes) -> ExtractionResult:
    """Extract from a CSV export."""
    text = content.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    header_text = " ".join(cell for row in rows[:8] for cell in row)
    return extract_bank_statement_rows(rows, header_text=header_text, cite="csv")


def _check_balance_continuity(transactions: list[BankTxn]) -> bool | None:
    """Every balance == previous + credit - debit (₹1 tolerance)."""
    if len(transactions) < 2:
        return None
    previous = transactions[0].balance
    if previous is None:
        return None
    for txn in transactions[1:]:
        if txn.balance is None:
            return None
        expected = previous + (txn.credit or Decimal(0)) - (txn.debit or Decimal(0))
        if abs(expected - txn.balance) > Decimal("1.0"):
            return False
        previous = txn.balance
    return True


def _monthly_aggregates(transactions: list[BankTxn]) -> list[MonthlyAggregates]:
    buckets: dict[str, MonthlyAggregates] = {}
    closing: dict[str, tuple[date, Decimal | None]] = defaultdict(lambda: (date.min, None))
    for txn in transactions:
        period = f"{txn.txn_date.year}-{txn.txn_date.month:02d}"
        bucket = buckets.setdefault(period, MonthlyAggregates(period=period))
        bucket.credits += txn.credit or Decimal(0)
        bucket.debits += txn.debit or Decimal(0)
        if _is_cheque_return(txn.narration):
            bucket.cheque_returns += 1
        last_date, _ = closing[period]
        if txn.txn_date >= last_date:
            closing[period] = (txn.txn_date, txn.balance)
    for period, bucket in buckets.items():
        bucket.closing_balance = closing[period][1]
    return [buckets[period] for period in sorted(buckets)]
