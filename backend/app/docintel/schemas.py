"""Typed extraction schemas for the initial document taxonomy (ARCHITECTURE §7).

Every extractor produces one of these models plus per-field confidence and
citations; the pipeline flattens them into append-only ``extracted_fields``
rows. Schemas are versioned — the version is stored on every row so old
extractions stay readable after schema evolution.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.docintel.citations import Citation, FieldIssue


class ExtractionResult(BaseModel):
    """What every extractor returns to the pipeline."""

    schema_id: str
    schema_version: str
    data: BaseModel
    field_confidence: dict[str, float] = Field(default_factory=dict)
    citations: dict[str, Citation] = Field(default_factory=dict)
    issues: list[FieldIssue] = Field(default_factory=list)

    def flatten(self) -> dict[str, Any]:
        """Dotted-path → JSON-safe value map for ``extracted_fields`` rows."""
        flat: dict[str, Any] = {}

        def walk(prefix: str, value: Any) -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    walk(f"{prefix}.{key}" if prefix else str(key), item)
            elif isinstance(value, list):
                flat[prefix] = value  # lists stay whole (transaction rows etc.)
            else:
                flat[prefix] = value

        walk("", self.data.model_dump(mode="json"))
        return flat


class TaxSplit(BaseModel):
    """IGST/CGST/SGST/cess amounts."""

    igst: Decimal = Decimal(0)
    cgst: Decimal = Decimal(0)
    sgst: Decimal = Decimal(0)
    cess: Decimal = Decimal(0)

    @property
    def total(self) -> Decimal:
        return self.igst + self.cgst + self.sgst + self.cess


class Gstr3bExtract(BaseModel):
    """FORM GSTR-3B monthly summary return (the fields credit analysis uses)."""

    gstin: str | None = None
    legal_name: str | None = None
    period: str | None = None  # YYYY-MM
    outward_taxable_value: Decimal | None = None  # table 3.1(a)
    outward_tax: TaxSplit = Field(default_factory=TaxSplit)
    itc_claimed: TaxSplit = Field(default_factory=TaxSplit)  # table 4(A)
    tax_paid_cash: Decimal | None = None
    tax_paid_itc: Decimal | None = None


class Gstr2bSupplierRow(BaseModel):
    """One supplier line of a GSTR-2B ITC statement."""

    supplier_gstin: str | None = None
    trade_name: str | None = None
    taxable_value: Decimal | None = None
    itc: TaxSplit = Field(default_factory=TaxSplit)


class Gstr2bExtract(BaseModel):
    """FORM GSTR-2B auto-drafted ITC statement (supplier-wise)."""

    gstin: str | None = None
    period: str | None = None  # YYYY-MM
    suppliers: list[Gstr2bSupplierRow] = Field(default_factory=list)
    itc_available: TaxSplit = Field(default_factory=TaxSplit)


class BankTxn(BaseModel):
    """One bank-statement transaction row."""

    txn_date: date
    narration: str
    debit: Decimal | None = None
    credit: Decimal | None = None
    balance: Decimal | None = None
    channel: str = "OTHER"  # NEFT|RTGS|IMPS|UPI|CHEQUE|CASH|CHARGES|OTHER


class MonthlyAggregates(BaseModel):
    """Per-month roll-up the validators and ratio engine consume."""

    period: str  # YYYY-MM
    credits: Decimal = Decimal(0)
    debits: Decimal = Decimal(0)
    cheque_returns: int = 0
    closing_balance: Decimal | None = None


class BankStatementExtract(BaseModel):
    """Bank statement: transaction rows plus computed monthly aggregates."""

    account_number_masked: str | None = None  # PII: masked at extraction (§15.6)
    ifsc: str | None = None
    period_start: date | None = None
    period_end: date | None = None
    transactions: list[BankTxn] = Field(default_factory=list)
    monthly: list[MonthlyAggregates] = Field(default_factory=list)
    balance_continuity_ok: bool | None = None


class ItrExtract(BaseModel):
    """Income-tax return acknowledgement / computation (key figures)."""

    pan: str | None = None
    assessment_year: str | None = None  # e.g. 2025-26
    gross_total_income: Decimal | None = None
    total_income: Decimal | None = None
    total_tax_paid: Decimal | None = None
    business_turnover: Decimal | None = None


class FinancialLineItems(BaseModel):
    """Canonical statement line items (LLM-backed extraction, Phase-4 seam).

    The Phase-6 ratio engine consumes this shape; extraction populates what
    the document supports and leaves the rest None (never zero-filled).
    """

    fiscal_year: str | None = None
    revenue: Decimal | None = None
    ebitda: Decimal | None = None
    net_profit: Decimal | None = None
    total_assets: Decimal | None = None
    total_equity: Decimal | None = None
    total_debt: Decimal | None = None
    current_assets: Decimal | None = None
    current_liabilities: Decimal | None = None
    inventory: Decimal | None = None
    receivables: Decimal | None = None
    payables: Decimal | None = None
    cash_and_equivalents: Decimal | None = None
    interest_expense: Decimal | None = None
    cash_flow_from_operations: Decimal | None = None


class FinancialStatementExtract(BaseModel):
    """Balance sheet / P&L / cash-flow extraction across fiscal years."""

    statement_type: str | None = None  # balance_sheet | profit_loss | cash_flow
    entity_name: str | None = None
    years: list[FinancialLineItems] = Field(default_factory=list)


class DocumentClassification(BaseModel):
    """Tier-2 classifier output (Gemini Flash, enum-constrained)."""

    doc_type: str
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
