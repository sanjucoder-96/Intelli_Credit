"""Deterministic CAM → PDF renderer (ADR-0011).

Consumes the :class:`~app.analysis.cam.CamReport` IR and lays it out as a
professional Credit Appraisal Memo. It computes NOTHING — every figure (grade,
decision, score, limit, rate, ratios, factor contributions) is printed verbatim
from the IR that the deterministic engine already produced (invariant #1/#3/#7).
The LLM's only contribution is the executive-summary prose slot.

Uses fpdf2 with the core Helvetica font, so text is sanitised to latin-1 (the
rupee sign becomes ``Rs.``) — no external font asset is required.
"""

from __future__ import annotations

from typing import Any

from fpdf import FPDF

from app.analysis.cam import CamReport

# Muted, print-friendly palette (RGB).
_INK = (24, 24, 27)
_MUTED = (113, 113, 122)
_RULE = (212, 212, 216)
_GREEN = (21, 128, 61)
_AMBER = (180, 120, 10)
_RED = (185, 28, 28)


def _latin1(text: str) -> str:
    """Make a string safe for the core (latin-1) fonts."""
    replacements = {
        "₹": "Rs. ",  # ₹
        "—": "-",  # —
        "–": "-",  # –
        "→": "->",  # →
        "↑": "+",  # ↑
        "↓": "-",  # ↓
        "•": "-",  # •
        "×": "x",  # ×
        "≤": "<=",
        "≥": ">=",
        "“": '"',
        "”": '"',
        "’": "'",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


def _inr(value: Any) -> str:
    if value is None:
        return "-"
    try:
        n = float(value)
    except (TypeError, ValueError):
        return str(value)
    return f"Rs. {n:,.0f}"


def _grade_color(grade: str | None) -> tuple[int, int, int]:
    if not grade:
        return _MUTED
    digits = "".join(c for c in grade if c.isdigit())
    n = int(digits) if digits else 10
    if n <= 3:
        return _GREEN
    if n <= 6:
        return _AMBER
    return _RED


def _decision_color(decision: str | None) -> tuple[int, int, int]:
    return {"approve": _GREEN, "refer": _AMBER, "decline": _RED}.get(decision or "", _MUTED)


class _Cam(FPDF):
    company: str = ""

    def header(self) -> None:  # noqa: D401 - fpdf hook
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*_MUTED)
        self.cell(0, 6, _latin1(f"Credit Appraisal Memo - {self.company}"), align="L")
        self.ln(8)

    def footer(self) -> None:  # noqa: D401 - fpdf hook
        self.set_y(-14)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*_MUTED)
        self.cell(
            0,
            5,
            _latin1(
                "Assembled deterministically from the risk engine (ADR-0009/0010); "
                "figures are not recomputed. "
            )
            + f"Page {self.page_no()}",
            align="C",
        )


def render_cam_pdf(cam: CamReport) -> bytes:
    """Render a CamReport IR into a professional PDF; returns the bytes."""
    pdf = _Cam(format="A4")
    pdf.company = cam.company.legal_name
    pdf.set_auto_page_break(True, margin=16)
    pdf.set_margins(16, 16, 16)
    pdf.add_page()

    _title_block(pdf, cam)
    _recommendation(pdf, cam)
    _executive_summary(pdf, cam)
    _five_cs(pdf, cam)
    _financials(pdf, cam)
    _findings(pdf, cam)
    _score_breakdown(pdf, cam)
    _overlays_and_notes(pdf, cam)

    return bytes(pdf.output())


# --- section renderers ----------------------------------------------------
def _heading(pdf: _Cam, n: int, title: str) -> None:
    pdf.ln(3)
    pdf.set_font("Helvetica", "B", 11)
    pdf.set_text_color(*_INK)
    pdf.cell(0, 7, _latin1(f"{n:02d}   {title}"))
    pdf.ln(7)
    pdf.set_draw_color(*_RULE)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
    pdf.ln(2)


def _kv(pdf: _Cam, label: str, value: str, value_color: tuple[int, int, int] = _INK) -> None:
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_MUTED)
    pdf.cell(55, 6, _latin1(label))
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_text_color(*value_color)
    pdf.cell(0, 6, _latin1(value))
    pdf.ln(6)


def _para(pdf: _Cam, text: str) -> None:
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_INK)
    pdf.multi_cell(0, 5, _latin1(text))


def _title_block(pdf: _Cam, cam: CamReport) -> None:
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(*_MUTED)
    pdf.cell(0, 5, _latin1("CREDIT APPRAISAL MEMO"))
    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 17)
    pdf.set_text_color(*_INK)
    pdf.cell(0, 9, _latin1(cam.company.legal_name))
    pdf.ln(9)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_MUTED)
    ident = " | ".join(p for p in [cam.company.sector, cam.company.gstin, cam.company.cin] if p)
    if ident:
        pdf.cell(0, 5, _latin1(ident))
        pdf.ln(6)
    # Grade + decision chips.
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(*_grade_color(cam.grade))
    pdf.cell(24, 7, _latin1(cam.grade or "-"))
    pdf.set_text_color(*_decision_color(cam.decision))
    pdf.cell(0, 7, _latin1((cam.decision or "").upper()))
    pdf.ln(9)


def _recommendation(pdf: _Cam, cam: CamReport) -> None:
    _heading(pdf, 1, "Recommendation")
    _kv(pdf, "Decision", (cam.decision or "-").upper(), _decision_color(cam.decision))
    _kv(pdf, "Risk grade", cam.grade or "-", _grade_color(cam.grade))
    _kv(pdf, "Final score", f"{cam.final_score:.1f} / 100  (coverage {cam.coverage:.0%})")
    _kv(pdf, "Recommended limit", _inr(cam.recommended_limit))
    if cam.limit_basis:
        _kv(pdf, "Binding constraint", cam.limit_basis)
    if cam.interest_rate_pct is not None:
        _kv(pdf, "Indicative rate", f"{cam.interest_rate_pct:.2f}%")
    if cam.rate_build_up:
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*_MUTED)
        pdf.multi_cell(0, 4.5, _latin1(cam.rate_build_up))


def _executive_summary(pdf: _Cam, cam: CamReport) -> None:
    if not cam.executive_summary:
        return
    _heading(pdf, 2, "Executive summary")
    _para(pdf, cam.executive_summary)


def _five_cs(pdf: _Cam, cam: CamReport) -> None:
    creditcs = [
        c
        for c in (cam.five_cs.model_dump() if hasattr(cam.five_cs, "model_dump") else {}).values()
        if isinstance(c, dict) and "name" in c
    ]
    if not creditcs:
        return
    _heading(pdf, 3, "Five Cs of credit")
    for c in creditcs:
        score = c.get("score")
        score_text = f"{score:.0f}/100" if score is not None else "n/a"
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_INK)
        pdf.cell(0, 6, _latin1(f"{str(c.get('name', '')).title()}   {score_text}"))
        pdf.ln(6)
        drivers = ", ".join(c.get("drivers", []) or [])
        if drivers:
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(0, 4.5, _latin1(drivers))


def _financials(pdf: _Cam, cam: CamReport) -> None:
    ratios = list(cam.ratios.available_ratios().items())
    if not ratios:
        return
    _heading(pdf, 4, "Financial ratios")
    pdf.set_font("Helvetica", "", 9)
    col = 0
    for name, value in ratios:
        pdf.set_text_color(*_MUTED)
        pdf.cell(35, 6, _latin1(name.replace("_", " ").title()))
        pdf.set_text_color(*_INK)
        pdf.cell(25, 6, _latin1(f"{value:.2f}"))
        col += 1
        if col % 2 == 0:
            pdf.ln(6)
    if col % 2 != 0:
        pdf.ln(6)


def _findings(pdf: _Cam, cam: CamReport) -> None:
    _heading(pdf, 5, "Key findings & risks")
    if not cam.findings:
        _para(pdf, "No adverse findings recorded.")
        return
    for f in cam.findings:
        sev = (f.severity or "info").lower()
        color = {"critical": _RED, "high": _RED, "medium": _AMBER}.get(sev, _MUTED)
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*color)
        pdf.cell(0, 6, _latin1(f"[{sev.upper()}]  {f.title}"))
        pdf.ln(6)
        if f.narrative:
            pdf.set_font("Helvetica", "", 8)
            pdf.set_text_color(*_MUTED)
            pdf.multi_cell(0, 4.5, _latin1(f.narrative))


def _score_breakdown(pdf: _Cam, cam: CamReport) -> None:
    if not cam.score_breakdown:
        return
    _heading(pdf, 6, "Score breakdown")
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_text_color(*_MUTED)
    pdf.cell(55, 6, _latin1("Pillar / factor"))
    pdf.cell(30, 6, _latin1("Input"))
    pdf.cell(30, 6, _latin1("Normalized"))
    pdf.cell(0, 6, _latin1("Contribution"))
    pdf.ln(6)
    for f in cam.score_breakdown:
        pdf.set_font("Helvetica", "", 8)
        pdf.set_text_color(*_INK)
        label = f"{f.get('pillar_id')} / {f.get('factor_id')}"
        pdf.cell(55, 5.5, _latin1(label[:38]))
        if f.get("admitted"):
            raw = f"{float(f['raw_value']):.3f}" if f.get("raw_value") is not None else "-"
            norm = f"{float(f['normalized']):.1f}" if f.get("normalized") is not None else "-"
            pdf.cell(30, 5.5, _latin1(raw))
            pdf.cell(30, 5.5, _latin1(norm))
            pdf.cell(0, 5.5, _latin1(f"{float(f.get('contribution') or 0):.2f}"))
        else:
            pdf.set_text_color(*_MUTED)
            pdf.cell(0, 5.5, _latin1(f"excluded: {f.get('exclusion_reason') or 'unavailable'}"))
        pdf.ln(5.5)


def _overlays_and_notes(pdf: _Cam, cam: CamReport) -> None:
    fired = [o for o in (cam.overlay_log or []) if o.get("fired")]
    if fired:
        _heading(pdf, 7, "Overlays & ML default-risk")
        for o in fired:
            rule = str(o.get("rule_id", "")).replace("_", " ").title()
            effect = str(o.get("effect", ""))
            detail = o.get("detail")
            pdf.set_font("Helvetica", "", 9)
            pdf.set_text_color(*_INK)
            text = f"{rule}  ({effect})" + (f" - {detail}" if detail else "")
            pdf.multi_cell(0, 6, _latin1(text))
    if cam.notes:
        pdf.ln(2)
        pdf.set_font("Helvetica", "I", 8)
        pdf.set_text_color(*_MUTED)
        for note in cam.notes:
            pdf.multi_cell(0, 4.5, _latin1(f"- {note}"))
