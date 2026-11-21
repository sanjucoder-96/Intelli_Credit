"""Credit Appraisal Memo — intermediate representation + deterministic render.

The CAM IR carries the authoritative NUMBERS (grade, decision, limit, rate,
ratios, the score breakdown, overlays) assembled deterministically from the
risk result. The LLM (cam_generator, Chunk D) may fill narrative slots, but it
never sets a number — the renderer prints the IR's figures verbatim, so the
memo cannot drift from the deterministic decision (invariant #1, #3). The
Phase-7 report renderers (PDF/DOCX/PPTX) consume this same IR.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.analysis.five_cs import FiveCs
from app.analysis.ratios import FinancialRatios
from app.analysis.scorecard import RiskResult


class CompanyProfile(BaseModel):
    legal_name: str
    cin: str | None = None
    gstin: str | None = None
    sector: str | None = None


class CamFinding(BaseModel):
    title: str
    severity: str
    category: str | None = None
    narrative: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class CamReport(BaseModel):
    """The full CAM IR."""

    company: CompanyProfile
    requested_amount: Decimal | None = None
    currency: str = "INR"
    executive_summary: str = ""  # LLM narrative slot (numbers stay in the sections)
    grade: str
    decision: str
    final_score: float
    coverage: float
    recommended_limit: Decimal | None = None
    limit_basis: str | None = None
    interest_rate_pct: float | None = None
    rate_build_up: str = ""
    five_cs: FiveCs
    ratios: FinancialRatios
    findings: list[CamFinding] = Field(default_factory=list)
    score_breakdown: list[dict[str, Any]] = Field(default_factory=list)
    overlay_log: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def assemble_cam(
    *,
    company: CompanyProfile,
    risk: RiskResult,
    ratios: FinancialRatios,
    five_cs: FiveCs,
    findings: list[CamFinding],
    requested_amount: Decimal | None = None,
    currency: str = "INR",
    executive_summary: str = "",
) -> CamReport:
    """Assemble the CAM IR from deterministic components."""
    rate = risk.pricing.rate_pct
    build_up = ""
    if rate is not None:
        parts = [
            f"base {risk.pricing.base_rate_pct:.2f}%",
            f"grade premium {risk.pricing.grade_premium_pct:.2f}%",
        ]
        parts += [f"{name} +{adj:.2f}%" for name, adj in risk.pricing.adjustments]
        build_up = " + ".join(parts) + f" = {rate:.2f}%"
    return CamReport(
        company=company,
        requested_amount=requested_amount,
        currency=currency,
        executive_summary=executive_summary,
        grade=risk.grade,
        decision=risk.decision.value,
        final_score=risk.final_score,
        coverage=risk.coverage,
        recommended_limit=risk.limit.amount,
        limit_basis=risk.limit.arithmetic,
        interest_rate_pct=rate,
        rate_build_up=build_up,
        five_cs=five_cs,
        ratios=ratios,
        findings=findings,
        score_breakdown=[f.model_dump() for f in risk.breakdown],
        overlay_log=[o.model_dump() for o in risk.overlay_log],
        notes=risk.notes,
    )


def render_markdown(cam: CamReport) -> str:
    """Deterministic Markdown render of the CAM IR (numbers verbatim)."""
    lines: list[str] = []
    lines.append(f"# Credit Appraisal Memo — {cam.company.legal_name}")
    if cam.company.gstin or cam.company.cin:
        ids = " · ".join(filter(None, [cam.company.cin, cam.company.gstin]))
        lines.append(f"*{ids}*")
    lines.append("")
    lines.append("## Recommendation")
    lines.append(
        f"- **Decision:** {cam.decision.upper()}  ·  **Grade:** {cam.grade}  "
        f"·  **Score:** {cam.final_score:.1f}/100  ·  **Coverage:** {cam.coverage:.0%}"
    )
    if cam.recommended_limit is not None:
        lines.append(
            f"- **Recommended limit:** {cam.currency} {cam.recommended_limit:,} ({cam.limit_basis})"
        )
    if cam.interest_rate_pct is not None:
        lines.append(f"- **Indicative rate:** {cam.interest_rate_pct:.2f}%  ({cam.rate_build_up})")
    if cam.executive_summary:
        lines.append("")
        lines.append("## Executive summary")
        lines.append(cam.executive_summary)
    lines.append("")
    lines.append("## Five Cs of Credit")
    for c in cam.five_cs.as_list():
        score = f"{c.score:.0f}/100" if c.score is not None else "n/a"
        lines.append(f"- **{c.name}** ({score}): {', '.join(c.drivers) or '—'}")
    lines.append("")
    lines.append("## Financial ratios")
    available = cam.ratios.available_ratios()
    if available:
        for name, value in available.items():
            lines.append(f"- {name.replace('_', ' ')}: {value:.2f}")
    else:
        lines.append("- No financial statements were available to compute ratios.")
    lines.append("")
    lines.append("## Key findings")
    if cam.findings:
        for finding in cam.findings:
            lines.append(
                f"- [{finding.severity.upper()}] {finding.title}"
                + (f" — {finding.narrative}" if finding.narrative else "")
            )
    else:
        lines.append("- No adverse findings recorded.")
    if cam.overlay_log:
        lines.append("")
        lines.append("## Overlays applied")
        for overlay in cam.overlay_log:
            if overlay.get("fired"):
                detail = overlay.get("detail", "")
                lines.append(f"- {overlay['rule_id']}: {overlay['effect']} {detail}".rstrip())
    if cam.notes:
        lines.append("")
        lines.append("## Notes")
        for note in cam.notes:
            lines.append(f"- {note}")
    return "\n".join(lines)
