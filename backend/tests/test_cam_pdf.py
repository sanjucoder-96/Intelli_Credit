"""CAM PDF renderer (Phase 7 hardening, ADR-0011).

Verifies the renderer prints the CAM IR verbatim — it never recomputes a score,
grade, limit, or rate. Renders a real CamReport (assembled from the deterministic
engine), extracts the PDF text, and asserts the IR figures appear; a structural
check confirms the renderer does not import the scoring engine.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pypdfium2 as pdfium

from app.analysis.cam import CamFinding, CompanyProfile, assemble_cam
from app.analysis.cam_pdf import render_cam_pdf
from app.analysis.five_cs import derive_five_cs
from app.analysis.ratios import compute_ratios
from app.analysis.scorecard import ScoreInputs, credit_appraisal_scorecard, evaluate_scorecard
from app.docintel.schemas import FinancialLineItems


def _strong_cam():  # noqa: ANN202
    fin = FinancialLineItems(
        fiscal_year="2025-26",
        revenue=Decimal("100000000"),
        ebitda=Decimal("18000000"),
        net_profit=Decimal("9000000"),
        total_assets=Decimal("120000000"),
        total_equity=Decimal("60000000"),
        total_debt=Decimal("30000000"),
        current_assets=Decimal("50000000"),
        current_liabilities=Decimal("25000000"),
        inventory=Decimal("15000000"),
        receivables=Decimal("20000000"),
        interest_expense=Decimal("3000000"),
        cash_flow_from_operations=Decimal("15000000"),
    )
    ratios = compute_ratios(fin)
    inputs = ScoreInputs(
        ratios=ratios.available_ratios(), financials=fin, turnover=fin.revenue, ml_pd=0.03
    )
    risk = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    five = derive_five_cs(risk, ratios, inputs)
    return assemble_cam(
        company=CompanyProfile(
            legal_name="Acme Textiles", sector="Textiles", gstin="27AAACA1234A1Z5"
        ),
        risk=risk,
        ratios=ratios,
        five_cs=five,
        findings=[CamFinding(title="GSTR-2B vs 3B ITC mismatch", severity="medium")],
        executive_summary="Strong borrower with healthy coverage and liquidity.",
    )


def _text(pdf: bytes) -> str:
    doc = pdfium.PdfDocument(pdf)
    out = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    doc.close()
    return out


def test_pdf_is_valid_and_prints_ir_values() -> None:
    cam = _strong_cam()
    pdf = render_cam_pdf(cam)
    assert pdf[:5] == b"%PDF-"
    text = _text(pdf)
    # every figure comes straight from the IR — no recomputation in the renderer
    assert cam.grade in text
    assert cam.decision.upper() in text
    assert f"{cam.final_score:.1f}" in text
    assert cam.interest_rate_pct is not None
    assert f"{cam.interest_rate_pct:.2f}%" in text
    assert f"{int(cam.recommended_limit):,}" in text  # e.g. 18,750,000
    assert "Acme Textiles" in text
    assert "GSTR-2B vs 3B ITC mismatch" in text


def test_pdf_reflects_ir_changes_verbatim() -> None:
    cam = _strong_cam()
    baseline = render_cam_pdf(cam)
    cam.grade = "IC9"
    cam.decision = "decline"
    changed = render_cam_pdf(cam)
    assert baseline != changed
    text = _text(changed)
    assert "IC9" in text and "DECLINE" in text


def test_renderer_does_not_import_the_scoring_engine() -> None:
    src = inspect.getsource(inspect.getmodule(render_cam_pdf))
    assert "evaluate_scorecard" not in src
    assert "compute_ratios" not in src
