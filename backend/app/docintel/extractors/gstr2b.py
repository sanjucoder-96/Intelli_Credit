"""Deterministic GSTR-2B extractor (supplier-wise ITC statement).

GSTR-2B is the auto-drafted input-tax-credit statement; its supplier table is
the counterparty ledger the ITC-mismatch validator needs (the brief's named
Indian-context check: GSTR-2A/2B ↔ 3B reconciliation). Parses table rows when
layout extraction provided them, falling back to line parsing.
"""

from __future__ import annotations

from decimal import Decimal

from app.docintel.citations import Citation, FieldIssue
from app.docintel.parsing import GSTIN_PATTERN, parse_amount, parse_period
from app.docintel.schemas import ExtractionResult, Gstr2bExtract, Gstr2bSupplierRow, TaxSplit

SCHEMA_ID = "gstr2b"
SCHEMA_VERSION = "1"


def _supplier_from_cells(cells: list[str]) -> Gstr2bSupplierRow | None:
    """Map one table row to a supplier line: GSTIN, name, amounts."""
    gstin = None
    name = None
    amounts: list[Decimal] = []
    for cell in cells:
        cell = (cell or "").strip()
        if not cell:
            continue
        if gstin is None and GSTIN_PATTERN.fullmatch(cell):
            gstin = cell
            continue
        amount = parse_amount(cell)
        if amount is not None:
            amounts.append(amount)
        elif name is None and not cell.isdigit():
            name = cell
    if gstin is None or not amounts:
        return None
    taxable = amounts[0]
    igst = amounts[1] if len(amounts) > 1 else Decimal(0)
    cgst = amounts[2] if len(amounts) > 2 else Decimal(0)
    sgst = amounts[3] if len(amounts) > 3 else Decimal(0)
    return Gstr2bSupplierRow(
        supplier_gstin=gstin,
        trade_name=name,
        taxable_value=taxable,
        itc=TaxSplit(igst=igst, cgst=cgst, sgst=sgst),
    )


def extract_gstr2b(
    page_texts: list[str], tables: list[tuple[int, list[list[str]]]]
) -> ExtractionResult:
    """Extract GSTR-2B from page texts + (page, cell-grid) tables."""
    citations: dict[str, Citation] = {}
    confidence: dict[str, float] = {}
    issues: list[FieldIssue] = []

    own_gstin: str | None = None
    period: str | None = None
    for page_number, text in enumerate(page_texts, start=1):
        for line in text.splitlines()[:40]:
            if own_gstin is None:
                match = GSTIN_PATTERN.search(line)
                if match:
                    own_gstin = match.group(0)
                    citations["gstin"] = Citation(page=page_number, quote=line.strip()[:200])
                    confidence["gstin"] = 0.95
            if period is None:
                period = parse_period(line)
                if period:
                    citations["period"] = Citation(page=page_number, quote=line.strip()[:200])
                    confidence["period"] = 0.9
        if own_gstin and period:
            break

    suppliers: list[Gstr2bSupplierRow] = []
    for page_number, grid in tables:
        for row in grid:
            supplier = _supplier_from_cells([str(cell) if cell else "" for cell in row])
            if supplier is not None and supplier.supplier_gstin != own_gstin:
                suppliers.append(supplier)
                citations[f"suppliers[{len(suppliers) - 1}]"] = Citation(
                    page=page_number, quote=(supplier.trade_name or supplier.supplier_gstin or "")
                )

    total = TaxSplit(
        igst=sum((s.itc.igst for s in suppliers), Decimal(0)),
        cgst=sum((s.itc.cgst for s in suppliers), Decimal(0)),
        sgst=sum((s.itc.sgst for s in suppliers), Decimal(0)),
    )
    if suppliers:
        confidence["itc_available"] = 0.85
    else:
        issues.append(
            FieldIssue(
                field_path="suppliers",
                message="No supplier ITC rows recognized",
                severity="error",
            )
        )

    return ExtractionResult(
        schema_id=SCHEMA_ID,
        schema_version=SCHEMA_VERSION,
        data=Gstr2bExtract(
            gstin=own_gstin, period=period, suppliers=suppliers, itc_available=total
        ),
        field_confidence=confidence,
        citations=citations,
        issues=issues,
    )
