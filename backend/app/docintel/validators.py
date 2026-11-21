"""Cross-document validators (ARCHITECTURE §7): the pipeline produces
*evidence*, agents later produce *judgment*.

Pure functions over typed extracts — no I/O, fully unit-testable. Each
validator declares the document types it needs and is skipped gracefully
when the case lacks them. Shipped set includes the two checks the hackathon
brief names explicitly: GSTR-2A/2B ↔ 3B ITC reconciliation and GSTR-3B ↔
bank-credit turnover reconciliation, plus ITR ↔ GST turnover and
cheque-return frequency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel

from app.docintel.citations import Citation
from app.docintel.schemas import (
    BankStatementExtract,
    Gstr2bExtract,
    Gstr3bExtract,
    ItrExtract,
)
from app.domain.enums import RiskCategory, Severity


@dataclass
class CaseExtracts:
    """Typed extraction outputs collected across one case's documents."""

    gstr3b: list[tuple[UUID, Gstr3bExtract]] = field(default_factory=list)
    gstr2b: list[tuple[UUID, Gstr2bExtract]] = field(default_factory=list)
    bank_statements: list[tuple[UUID, BankStatementExtract]] = field(default_factory=list)
    itr: list[tuple[UUID, ItrExtract]] = field(default_factory=list)


class ValidationOutcome(BaseModel):
    """One cross-document validation finding with citations into BOTH sources."""

    validator_id: str
    severity: Severity
    category: RiskCategory
    title: str
    narrative: str
    metrics: dict[str, str]
    period: str | None = None
    citations: list[tuple[UUID, Citation]] = []


def _pct(part: Decimal, whole: Decimal) -> Decimal:
    return (part / whole * 100).quantize(Decimal("0.1")) if whole else Decimal(0)


def validate_gst_vs_bank_credits(extracts: CaseExtracts) -> list[ValidationOutcome]:
    """GSTR-3B outward turnover vs bank credits, month by month.

    Declared turnover materially above banked credits suggests revenue
    inflation / fake billing; the reverse suggests undeclared receipts. The
    15% HIGH threshold follows the risk-engine design (ADR-0009 sources).
    """
    if not extracts.gstr3b or not extracts.bank_statements:
        return []

    banked: dict[str, Decimal] = {}
    bank_cites: dict[str, tuple[UUID, Citation]] = {}
    for document_id, statement in extracts.bank_statements:
        for month in statement.monthly:
            banked[month.period] = banked.get(month.period, Decimal(0)) + month.credits
            bank_cites[month.period] = (
                document_id,
                Citation(quote=f"monthly credits {month.period}: {month.credits}"),
            )

    outcomes: list[ValidationOutcome] = []
    for document_id, gstr in extracts.gstr3b:
        if not gstr.period or gstr.outward_taxable_value is None:
            continue
        credits = banked.get(gstr.period)
        if credits is None:
            continue
        declared = gstr.outward_taxable_value
        gap = declared - credits
        gap_pct = _pct(abs(gap), declared) if declared else Decimal(0)
        if gap_pct <= 15:
            continue
        severity = Severity.HIGH if gap_pct > 25 else Severity.MEDIUM
        citations = [(document_id, Citation(quote=f"3.1(a) outward: {declared}"))]
        if gstr.period in bank_cites:
            citations.append(bank_cites[gstr.period])
        outcomes.append(
            ValidationOutcome(
                validator_id="gst_vs_bank_credits",
                severity=severity,
                category=RiskCategory.FINANCIAL,
                title=f"GST turnover vs bank credits diverge {gap_pct}% in {gstr.period}",
                narrative=(
                    f"GSTR-3B declares outward taxable supplies of {declared} for "
                    f"{gstr.period} but bank statements show {credits} in credits — "
                    f"a {gap_pct}% gap "
                    f"({'declared exceeds banked' if gap > 0 else 'banked exceeds declared'}). "
                    "Possible revenue inflation, fake billing, or undeclared receipts."
                ),
                metrics={
                    "declared_turnover": str(declared),
                    "bank_credits": str(credits),
                    "gap_pct": str(gap_pct),
                },
                period=gstr.period,
                citations=citations,
            )
        )
    return outcomes


def validate_itc_mismatch(extracts: CaseExtracts) -> list[ValidationOutcome]:
    """GSTR-2B available ITC vs GSTR-3B claimed ITC (the brief's named check).

    Claiming materially more input-tax credit than suppliers reported
    (2B) indicates fake invoicing / non-existent suppliers.
    """
    if not extracts.gstr3b or not extracts.gstr2b:
        return []
    two_b_by_period = {g.period: (doc_id, g) for doc_id, g in extracts.gstr2b if g.period}

    outcomes: list[ValidationOutcome] = []
    for document_id, gstr3b in extracts.gstr3b:
        if not gstr3b.period or gstr3b.period not in two_b_by_period:
            continue
        two_b_doc, two_b = two_b_by_period[gstr3b.period]
        claimed = gstr3b.itc_claimed.total
        available = two_b.itc_available.total
        if claimed <= available or not claimed:
            continue
        excess = claimed - available
        excess_pct = _pct(excess, claimed)
        if excess_pct <= 5:
            continue  # timing differences are normal at the margin
        severity = Severity.CRITICAL if excess_pct > 20 else Severity.HIGH
        outcomes.append(
            ValidationOutcome(
                validator_id="gstr2b_vs_3b_itc_mismatch",
                severity=severity,
                category=RiskCategory.LEGAL,
                title=(f"ITC claimed exceeds ITC available by {excess_pct}% in {gstr3b.period}"),
                narrative=(
                    f"GSTR-3B claims input-tax credit of {claimed} for {gstr3b.period}, but "
                    f"GSTR-2B shows only {available} available from suppliers — an excess of "
                    f"{excess} ({excess_pct}%). Excess ITC claims indicate purchases from "
                    "non-filing or non-existent suppliers (fake invoicing exposure)."
                ),
                metrics={
                    "itc_claimed": str(claimed),
                    "itc_available": str(available),
                    "excess_pct": str(excess_pct),
                },
                period=gstr3b.period,
                citations=[
                    (document_id, Citation(quote=f"ITC claimed: {claimed}")),
                    (two_b_doc, Citation(quote=f"ITC available: {available}")),
                ],
            )
        )
    return outcomes


def validate_itr_vs_gst_turnover(extracts: CaseExtracts) -> list[ValidationOutcome]:
    """Annualized GST turnover vs ITR-declared business turnover."""
    if not extracts.itr or not extracts.gstr3b:
        return []
    gst_annual = sum(
        (g.outward_taxable_value or Decimal(0) for _, g in extracts.gstr3b), Decimal(0)
    )
    months = len({g.period for _, g in extracts.gstr3b if g.period})
    if not months or not gst_annual:
        return []
    annualized = gst_annual / months * 12

    outcomes: list[ValidationOutcome] = []
    for document_id, itr in extracts.itr:
        if itr.business_turnover is None or not itr.business_turnover:
            continue
        gap_pct = _pct(abs(annualized - itr.business_turnover), itr.business_turnover)
        if gap_pct <= 20:
            continue
        gstr_doc = extracts.gstr3b[0][0]
        outcomes.append(
            ValidationOutcome(
                validator_id="itr_vs_gst_turnover",
                severity=Severity.MEDIUM if gap_pct <= 40 else Severity.HIGH,
                category=RiskCategory.FINANCIAL,
                title=f"ITR turnover vs annualized GST turnover diverge {gap_pct}%",
                narrative=(
                    f"The ITR declares business turnover of {itr.business_turnover} while "
                    f"GST filings annualize to {annualized.quantize(Decimal('1'))} "
                    f"(from {months} month(s)) — a {gap_pct}% divergence between tax filings."
                ),
                metrics={
                    "itr_turnover": str(itr.business_turnover),
                    "gst_annualized": str(annualized.quantize(Decimal("1"))),
                    "gap_pct": str(gap_pct),
                },
                citations=[
                    (document_id, Citation(quote=f"ITR turnover {itr.business_turnover}")),
                    (
                        gstr_doc,
                        Citation(quote=f"GST annualized {annualized.quantize(Decimal('1'))}"),
                    ),
                ],
            )
        )
    return outcomes


def validate_cheque_returns(extracts: CaseExtracts) -> list[ValidationOutcome]:
    """Cheque-return frequency — a classic early-warning liquidity signal."""
    outcomes: list[ValidationOutcome] = []
    for document_id, statement in extracts.bank_statements:
        returns = sum(month.cheque_returns for month in statement.monthly)
        months = max(len(statement.monthly), 1)
        if returns == 0:
            continue
        # Multiply before dividing: Decimal(1)/3*3 is 0.999... and would
        # misclassify exact threshold hits.
        per_quarter = Decimal(returns) * 3 / Decimal(months)
        severity = (
            Severity.HIGH
            if per_quarter >= 3
            else Severity.MEDIUM
            if per_quarter >= 1
            else Severity.LOW
        )
        outcomes.append(
            ValidationOutcome(
                validator_id="cheque_return_frequency",
                severity=severity,
                category=RiskCategory.OPERATIONAL,
                title=f"{returns} cheque return(s) across {months} month(s)",
                narrative=(
                    f"The bank statement records {returns} inward/outward cheque returns over "
                    f"{months} month(s) (~{per_quarter.quantize(Decimal('0.1'))} per quarter) — "
                    "a recurring-liquidity or counterparty-quality early-warning signal."
                ),
                metrics={"returns": str(returns), "months": str(months)},
                citations=[(document_id, Citation(quote="cheque return narrations"))],
            )
        )
    return outcomes


CROSS_DOC_VALIDATORS = (
    validate_gst_vs_bank_credits,
    validate_itc_mismatch,
    validate_itr_vs_gst_turnover,
    validate_cheque_returns,
)


def run_all(extracts: CaseExtracts) -> list[ValidationOutcome]:
    """Run every registered validator; missing inputs skip silently."""
    outcomes: list[ValidationOutcome] = []
    for validator in CROSS_DOC_VALIDATORS:
        outcomes.extend(validator(extracts))
    return outcomes
