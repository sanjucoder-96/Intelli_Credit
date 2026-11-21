"""Phase-8 CAM consistency (priority #6, deterministic surfaces).

The CAM must never carry a number that differs from the authoritative
``RiskResult``. These tests pin the assemble-time identity (every figure in the
CAM IR is copied verbatim from the RiskResult), a golden fixture (so silent
drift in the deterministic engine is caught), and that the Markdown/PDF renders
echo the same figures. The API==persisted surface parity (which needs a live
DB) is covered in ``test_cam_api_consistency.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pypdfium2 as pdfium

from app.analysis.cam import CamFinding, CompanyProfile, assemble_cam, render_markdown
from app.analysis.cam_pdf import render_cam_pdf
from app.analysis.five_cs import derive_five_cs
from app.analysis.ratios import compute_ratios
from app.analysis.scorecard import ScoreInputs, credit_appraisal_scorecard, evaluate_scorecard
from app.docintel.schemas import FinancialLineItems

_STRONG = FinancialLineItems(
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


def _risk_and_cam():  # noqa: ANN202
    ratios = compute_ratios(_STRONG)
    inputs = ScoreInputs(
        ratios=ratios.available_ratios(), financials=_STRONG, turnover=_STRONG.revenue, ml_pd=0.03
    )
    risk = evaluate_scorecard(credit_appraisal_scorecard(), inputs)
    five = derive_five_cs(risk, ratios, inputs)
    cam = assemble_cam(
        company=CompanyProfile(legal_name="Acme Textiles", gstin="27AAACA1234A1Z5"),
        risk=risk,
        ratios=ratios,
        five_cs=five,
        findings=[CamFinding(title="GSTR-2B vs 3B ITC mismatch", severity="medium")],
        executive_summary="Strong borrower.",
    )
    return risk, cam


def _pdf_text(pdf: bytes) -> str:
    doc = pdfium.PdfDocument(pdf)
    out = "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    doc.close()
    return out


# --------------------------------------------------------------------------- #
# Assemble-time identity: the CAM copies every authoritative number verbatim.
# --------------------------------------------------------------------------- #


def test_cam_ir_numbers_equal_the_risk_result_exactly() -> None:
    risk, cam = _risk_and_cam()
    assert cam.grade == risk.grade
    assert cam.decision == risk.decision.value
    assert cam.final_score == risk.final_score
    assert cam.coverage == risk.coverage
    assert cam.recommended_limit == risk.limit.amount
    assert cam.limit_basis == risk.limit.arithmetic
    assert cam.interest_rate_pct == risk.pricing.rate_pct
    # Structured breakdown/overlays are the same objects' dumps, not a re-derivation.
    assert cam.score_breakdown == [f.model_dump() for f in risk.breakdown]
    assert cam.overlay_log == [o.model_dump() for o in risk.overlay_log]
    assert cam.notes == risk.notes


def test_cam_re_derivation_is_deterministic() -> None:
    # The CAM agent re-derives numbers from the SAME ScoreInputs. Determinism
    # guarantees the re-derivation cannot diverge from the persisted assessment.
    (risk1, cam1), (risk2, cam2) = _risk_and_cam(), _risk_and_cam()
    assert cam1.model_dump() == cam2.model_dump()
    assert risk1.model_dump() == risk2.model_dump()


# --------------------------------------------------------------------------- #
# Golden fixture: pins the deterministic engine's output for a known borrower.
# --------------------------------------------------------------------------- #


def test_golden_strong_borrower_fixture() -> None:
    _risk, cam = _risk_and_cam()
    assert cam.grade == "IC1"
    assert cam.decision == "approve"
    assert cam.final_score == 86.23
    assert cam.coverage == 1.0
    assert cam.recommended_limit == Decimal("18750000")
    assert cam.interest_rate_pct == 9.5
    assert len(cam.score_breakdown) == 9
    assert len(cam.overlay_log) == 0  # a clean borrower fires no overlays


# --------------------------------------------------------------------------- #
# Renders echo the IR figures (Markdown + PDF).
# --------------------------------------------------------------------------- #


def test_markdown_and_pdf_echo_the_same_ir_figures() -> None:
    _risk, cam = _risk_and_cam()
    md = render_markdown(cam)
    pdf_text = _pdf_text(render_cam_pdf(cam))
    for surface in (md, pdf_text):
        assert cam.grade in surface
        assert cam.decision.upper() in surface
        assert f"{cam.final_score:.1f}" in surface
        assert f"{cam.interest_rate_pct:.2f}%" in surface
    # The rupee limit appears (grouping differs between renderers, so match digits).
    assert "18,750,000" in md or "18750000" in md.replace(",", "")
    assert "18,750,000" in pdf_text
