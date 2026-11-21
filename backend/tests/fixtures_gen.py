"""Acceptance-fixture generators (test-only; fpdf2 + openpyxl).

Everything generated here is synthetic demo data mirroring the templated
layouts the deterministic extractors target: a born-digital GSTR-3B, a
*scanned* GSTR-3B (text rendered to an image-only PDF — the OCR path), a
GSTR-2B with a ruled supplier table (pdfplumber's lattice target), and a
bank-statement XLSX whose numbers deliberately trigger the cross-document
validators (GST↔bank gap, ITC mismatch, cheque returns).
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

GSTR3B_APRIL_LINES = (
    "FORM GSTR-3B",
    "[See rule 61(5)]",
    "GSTIN 27AAACA1234A1Z5",
    "Legal name ACME TEXTILES PRIVATE LIMITED",
    "Period 04-2026",
    "3.1 Details of Outward Supplies",
    "(a) Outward taxable supplies 1,00,00,000.00",
    "Integrated Tax 9,00,000.00",
    "Central Tax 0.00",
    "State/UT Tax 0.00",
    "Cess 0.00",
    "4. Eligible ITC",
    "(A) ITC Available - All other ITC - IGST 5,00,000.00",
    "Tax paid in cash 4,00,000.00",
    "Tax paid through ITC 5,00,000.00",
)

GSTR3B_MAY_LINES = tuple(
    line.replace("04-2026", "05-2026").replace("1,00,00,000.00", "95,00,000.00")
    for line in GSTR3B_APRIL_LINES
)


def _pdf_from_lines(lines: tuple[str, ...]) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in lines:
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")
    return bytes(pdf.output())


def gstr3b_born_digital() -> bytes:
    """April GSTR-3B with a real text layer (deterministic extraction path)."""
    return _pdf_from_lines(GSTR3B_APRIL_LINES)


def gstr3b_scanned() -> bytes:
    """May GSTR-3B as an image-only PDF (no text layer → the OCR path).

    Rendered from the born-digital PDF at high DPI, then embedded as a
    full-page image — exactly what a flat-bed scan of the form produces.
    """
    from fpdf import FPDF

    from app.ingestion.pdf import render_page_png

    png = render_page_png(_pdf_from_lines(GSTR3B_MAY_LINES), 1, dpi=220)
    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.image(io.BytesIO(png), x=0, y=0, w=210, h=297)  # full A4
    return bytes(pdf.output())


def gstr2b_with_table() -> bytes:
    """April GSTR-2B: header text + a ruled supplier table (pdfplumber target).

    ITC available totals 4,00,000 (2,70,000 + 1,30,000) against 5,00,000
    claimed in the April 3B fixture — a 20% excess claim, so the ITC-mismatch
    validator must fire at HIGH severity.
    """
    from fpdf import FPDF

    pdf = FPDF(format="A4")
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    for line in ("FORM GSTR-2B", "GSTIN 27AAACA1234A1Z5", "Period 04-2026", ""):
        pdf.cell(0, 8, line, new_x="LMARGIN", new_y="NEXT")

    header = ("GSTIN of supplier", "Trade name", "Taxable value", "IGST", "CGST", "SGST")
    rows = (
        ("29AACCN9012C1Z8", "Nimbus Logistics", "15,00,000", "2,70,000", "0", "0"),
        ("36AABCD5678B1Z3", "Deccan Agro", "8,00,000", "1,30,000", "0", "0"),
    )
    widths = (42, 38, 30, 28, 26, 26)
    pdf.set_font("Helvetica", size=9)
    for cell, width in zip(header, widths, strict=True):
        pdf.cell(width, 8, cell, border=1)
    pdf.ln()
    for row in rows:
        for cell, width in zip(row, widths, strict=True):
            pdf.cell(width, 8, cell, border=1)
        pdf.ln()
    return bytes(pdf.output())


def bank_statement_xlsx() -> bytes:
    """Apr-May statement: April credits 60,00,000 (vs 1,00,00,000 declared in
    the April 3B → 40% gap → HIGH gst_vs_bank finding) plus one cheque return.
    Balances are continuity-consistent so row confidence stays high."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Statement"
    sheet.append(["ACME TEXTILES PRIVATE LIMITED - Account Statement"])
    sheet.append(["A/C No: 001234567890  IFSC: HDFC0001234"])
    sheet.append([])
    sheet.append(["Date", "Narration", "Debit", "Credit", "Balance"])
    rows: tuple[tuple[Any, ...], ...] = (
        ("01/04/2026", "OPENING BALANCE", "", "", "10,00,000.00"),
        ("07/04/2026", "NEFT-CUSTOMER A PAYMENT", "", "35,00,000.00", "45,00,000.00"),
        ("15/04/2026", "RTGS-CUSTOMER B PAYMENT", "", "25,00,000.00", "70,00,000.00"),
        ("20/04/2026", "CHQ RTN - INSUFFICIENT FUNDS", "25,000.00", "", "69,75,000.00"),
        ("28/04/2026", "VENDOR PAYMENT NEFT", "30,00,000.00", "", "39,75,000.00"),
        ("05/05/2026", "UPI-CUSTOMER C", "", "20,00,000.00", "59,75,000.00"),
        ("18/05/2026", "SALARY BATCH", "12,00,000.00", "", "47,75,000.00"),
    )
    for row in rows:
        sheet.append(list(row))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def bundle_zip() -> bytes:
    """ZIP bundling the GSTR-2B PDF and a small CSV note (fan-out test)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("gstr2b_april.pdf", gstr2b_with_table())
        archive.writestr(
            "site_visit_note.txt",
            "Factory visit note: unit operating at reduced capacity; "
            "inventory levels consistent with declared turnover.",
        )
    return buffer.getvalue()
