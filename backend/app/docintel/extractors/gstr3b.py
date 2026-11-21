"""Deterministic GSTR-3B extractor.

FORM GSTR-3B is a templated statutory summary return — the layout is fixed
enough that labeled-line parsing over the page text is the honest, reliable
extractor (no model required). Confidence comes from arithmetic
post-validation: outward tax components summing sanely against the taxable
value, and cash+ITC settlement being present when tax is due.
"""

from __future__ import annotations

from decimal import Decimal

from app.docintel.citations import Citation, FieldIssue
from app.docintel.parsing import GSTIN_PATTERN, find_labeled_amount, parse_period
from app.docintel.schemas import ExtractionResult, Gstr3bExtract, TaxSplit

SCHEMA_ID = "gstr3b"
SCHEMA_VERSION = "1"

_LABELS = {
    "outward_taxable_value": r"3\.1.*\(a\)|outward\s+taxable\s+supplies",
    "igst": r"integrated\s+tax|IGST",
    "cgst": r"central\s+tax|CGST",
    "sgst": r"state/?ut\s+tax|SGST",
    "cess": r"\bcess\b",
    "itc_igst": r"ITC\s+Available.*IGST|all\s+other\s+ITC.*IGST",
    "tax_paid_cash": r"paid\s+in\s+cash|cash\s+ledger",
    "tax_paid_itc": r"paid\s+through\s+ITC|credit\s+ledger",
}


def extract_gstr3b(page_texts: list[str]) -> ExtractionResult:
    """Extract GSTR-3B fields from per-page text (text layer or OCR)."""
    lines: list[tuple[int, str]] = [
        (page_number, line)
        for page_number, text in enumerate(page_texts, start=1)
        for line in text.splitlines()
        if line.strip()
    ]
    flat_lines = [line for _, line in lines]

    citations: dict[str, Citation] = {}
    confidence: dict[str, float] = {}
    issues: list[FieldIssue] = []

    def cite(field: str, line_index: int) -> None:
        page, line = lines[line_index]
        citations[field] = Citation(page=page, quote=line.strip()[:200])

    gstin: str | None = None
    for index, line in enumerate(flat_lines):
        match = GSTIN_PATTERN.search(line)
        if match:
            gstin = match.group(0)
            cite("gstin", index)
            confidence["gstin"] = 0.95
            break

    period = None
    for index, line in enumerate(flat_lines[:40]):
        period = parse_period(line)
        if period:
            cite("period", index)
            confidence["period"] = 0.9
            break

    def labeled(field: str, pattern: str) -> Decimal | None:
        found = find_labeled_amount(flat_lines, pattern)
        if found is None:
            return None
        amount, line_index, _line = found
        cite(field, line_index)
        return amount

    outward = labeled("outward_taxable_value", _LABELS["outward_taxable_value"])
    outward_tax = TaxSplit(
        igst=labeled("outward_tax.igst", _LABELS["igst"]) or Decimal(0),
        cgst=labeled("outward_tax.cgst", _LABELS["cgst"]) or Decimal(0),
        sgst=labeled("outward_tax.sgst", _LABELS["sgst"]) or Decimal(0),
        cess=labeled("outward_tax.cess", _LABELS["cess"]) or Decimal(0),
    )
    itc_igst = labeled("itc_claimed.igst", _LABELS["itc_igst"]) or Decimal(0)
    tax_paid_cash = labeled("tax_paid_cash", _LABELS["tax_paid_cash"])
    tax_paid_itc = labeled("tax_paid_itc", _LABELS["tax_paid_itc"])

    data = Gstr3bExtract(
        gstin=gstin,
        period=period,
        outward_taxable_value=outward,
        outward_tax=outward_tax,
        itc_claimed=TaxSplit(igst=itc_igst),
        tax_paid_cash=tax_paid_cash,
        tax_paid_itc=tax_paid_itc,
    )

    # Deterministic post-validation drives confidence (never self-grading).
    if outward is None:
        issues.append(
            FieldIssue(
                field_path="outward_taxable_value",
                message="Table 3.1(a) outward taxable value not found",
                severity="error",
            )
        )
    else:
        confidence["outward_taxable_value"] = 0.9
        tax_total = outward_tax.total
        if outward > 0 and tax_total > 0:
            effective_rate = tax_total / outward
            if effective_rate > Decimal("0.5"):
                issues.append(
                    FieldIssue(
                        field_path="outward_tax",
                        message=f"Implausible effective tax rate {effective_rate:.2%}",
                    )
                )
                confidence["outward_taxable_value"] = 0.5
        settled = (tax_paid_cash or Decimal(0)) + (tax_paid_itc or Decimal(0))
        if tax_total > 0 and settled == 0:
            issues.append(
                FieldIssue(
                    field_path="tax_paid_cash",
                    message="Outward tax present but no cash/ITC settlement found",
                )
            )

    return ExtractionResult(
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        data=data,
        field_confidence=confidence,
        citations=citations,
        issues=issues,
    )
