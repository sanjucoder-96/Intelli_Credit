"""Attach a real demo document (annual report) with extracted fields + evidence.

Generates a text-based annual-report PDF whose value positions are recorded as
normalised bounding boxes, then attaches it to the completed demo case as a real
Document + ExtractedField + Evidence rows. This makes the frontend's document
list, extracted-fields view, evidence-viewer highlight, and financials pre-fill
work reliably against real backend rows (independent of live OCR/Gemini).

    ./.venv/Scripts/python.exe -m scripts.provision_documents
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from fpdf import FPDF

from app.core.config import get_settings
from app.domain.enums import (
    DataOrigin,
    DocumentStatus,
    EvidenceKind,
    IngestStage,
    StageStatus,
    WorkflowExecutionStatus,
)
from app.infrastructure.db.engine import get_session_factory
from app.infrastructure.db.models import (
    Document,
    DocumentStage,
    Evidence,
    ExtractedField,
    LoanApplication,
    Organization,
    WorkflowExecution,
)
from app.infrastructure.storage.local import LocalFilesystemStorage
from scripts.provision_demo import _run_async

# field -> (statement label, rupee value)
ROWS: list[tuple[str, str, int]] = [
    ("revenue", "Revenue from operations", 100000000),
    ("ebitda", "EBITDA", 18000000),
    ("net_profit", "Net profit after tax", 9000000),
    ("interest_expense", "Finance costs (interest)", 3000000),
    ("total_assets", "Total assets", 120000000),
    ("total_equity", "Total equity", 60000000),
    ("total_debt", "Total borrowings", 30000000),
    ("current_assets", "Current assets", 50000000),
    ("current_liabilities", "Current liabilities", 25000000),
    ("inventory", "Inventories", 15000000),
    ("receivables", "Trade receivables", 20000000),
    ("cash_flow_from_operations", "Cash flow from operations", 15000000),
]


def _build_pdf(company: str) -> tuple[bytes, dict[str, dict[str, Any]]]:
    pdf = FPDF(format="A4")
    pdf.set_margins(20, 20, 20)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 10, company)
    pdf.ln(9)
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90, 90, 90)
    pdf.cell(0, 6, "Annual Report - Financial Year 2025-26 (figures in Rs.)")
    pdf.ln(10)

    positions: dict[str, dict[str, Any]] = {}
    section = None
    for field, label, value in ROWS:
        new_section = "Statement of Profit & Loss" if field in {
            "revenue", "ebitda", "net_profit", "interest_expense"
        } else ("Cash Flow" if field == "cash_flow_from_operations" else "Balance Sheet")
        if new_section != section:
            section = new_section
            pdf.ln(3)
            pdf.set_font("Helvetica", "B", 11)
            pdf.set_text_color(24, 24, 27)
            pdf.cell(0, 7, section)
            pdf.ln(8)
        y = pdf.get_y()
        pdf.set_font("Helvetica", "", 11)
        pdf.set_text_color(24, 24, 27)
        pdf.cell(95, 8, label)
        x = pdf.get_x()
        pdf.cell(0, 8, f"{value:,}", align="L")
        pdf.ln(8)
        positions[field] = {
            "page": 1,
            "bbox": {
                "x0": round(x / pdf.w, 4),
                "y0": round(y / pdf.h, 4),
                "x1": round((x + 60) / pdf.w, 4),
                "y1": round((y + 8) / pdf.h, 4),
            },
            "quote": f"{label}: {value:,}",
        }
    return bytes(pdf.output()), positions


async def _provision() -> None:
    settings = get_settings()
    factory = get_session_factory()
    storage = LocalFilesystemStorage(settings.storage.root)

    async with factory() as session:
        org = (
            await session.execute(sa.select(Organization).where(Organization.slug == "demo-lender"))
        ).scalar_one()
        # the completed case (has a COMPLETED run)
        execution = (
            await session.execute(
                sa.select(WorkflowExecution)
                .where(
                    WorkflowExecution.org_id == org.id,
                    WorkflowExecution.status == WorkflowExecutionStatus.COMPLETED,
                )
                .order_by(WorkflowExecution.created_at.asc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if execution is None or execution.loan_application_id is None:
            print("No completed case found; run provision_demo first.")
            return
        case = await session.get(LoanApplication, execution.loan_application_id)
        assert case is not None
        company_id = case.company_id

    pdf_bytes, positions = _build_pdf("Acme Textiles Private Limited")
    blob = await storage.put(
        case_id=case.id, filename="annual_report_FY2025-26.pdf", data=pdf_bytes
    )

    async with factory() as session:
        # replace any prior demo document for idempotency
        prior = (
            await session.execute(
                sa.select(Document).where(
                    Document.loan_application_id == case.id,
                    Document.filename == "annual_report_FY2025-26.pdf",
                )
            )
        ).scalars().all()
        for d in prior:
            await session.delete(d)
        await session.flush()

        document = Document(
            org_id=org.id, loan_application_id=case.id, company_id=company_id,
            storage_key=blob.key, sha256=blob.sha256, filename="annual_report_FY2025-26.pdf",
            media_type="application/pdf", doc_type="annual_report", doc_type_confidence=0.96,
            status=DocumentStatus.READY, page_count=1, data_origin=DataOrigin.SYNTHETIC_DEMO,
        )
        session.add(document)
        await session.flush()

        for stage in (
            IngestStage.DETECT_TYPE, IngestStage.EXTRACT_TEXT_LAYER, IngestStage.CLASSIFY,
            IngestStage.EXTRACT_DOMAIN, IngestStage.CROSS_VALIDATE,
        ):
            session.add(
                DocumentStage(
                    document_id=document.id, stage=stage, status=StageStatus.DONE, attempt=1
                )
            )

        session.add(
            ExtractedField(
                org_id=org.id, document_id=document.id, schema_id="financial_statement",
                schema_version="1", field_path="years.0.fiscal_year",
                value={"value": "2025-26"}, confidence=0.98, page=1,
            )
        )
        for field, _label, value in ROWS:
            pos = positions[field]
            session.add(
                ExtractedField(
                    org_id=org.id, document_id=document.id, schema_id="financial_statement",
                    schema_version="1", field_path=f"years.0.{field}",
                    value={"value": value}, confidence=0.94, page=1,
                    bbox={"page": 1, "bboxes": [pos["bbox"]], "quote": pos["quote"]},
                )
            )
        # a couple of case-level evidence rows pointing at the document region
        for field in ("revenue", "total_debt"):
            pos = positions[field]
            session.add(
                Evidence(
                    org_id=org.id, loan_application_id=case.id, kind=EvidenceKind.DOCUMENT,
                    claim=pos["quote"], document_id=document.id, page=1,
                    bboxes={"page": 1, "bboxes": [pos["bbox"]]}, quote=pos["quote"],
                    source_tier=1.0, confidence=0.94, created_by_agent="annual_report_extractor",
                )
            )
        await session.commit()
    print(f"attached annual_report to case {case.id} ({len(ROWS)} extracted fields, 2 evidence)")


if __name__ == "__main__":
    _run_async(_provision())
