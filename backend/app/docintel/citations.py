"""Citation primitives — every extracted fact points back at its source.

Granularity policy (ADR-0006): word/line-level bounding boxes where the
parser provides geometry (pdfplumber words, RapidOCR lines), page-level plus
exact quote where it cannot (Gemini-vision pages), and sheet/cell references
for spreadsheets. Coordinates are normalized to [0, 1] page space so the
viewer overlay is resolution-independent.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BBox(BaseModel):
    """Normalized page-space rectangle (x0, y0, x1, y1 in [0, 1])."""

    x0: float
    y0: float
    x1: float
    y1: float


class Citation(BaseModel):
    """Where one extracted value came from inside its document."""

    page: int | None = None
    bboxes: list[BBox] = Field(default_factory=list)
    quote: str | None = None
    sheet: str | None = None
    cell: str | None = None

    def brief(self) -> str:
        """Human-readable one-liner for findings/narratives."""
        if self.sheet is not None:
            return f"{self.sheet}!{self.cell}" if self.cell else self.sheet
        if self.page is not None:
            return f"p.{self.page}"
        return "document"


class FieldIssue(BaseModel):
    """One deterministic post-validation problem on an extracted field."""

    field_path: str
    message: str
    severity: str = "warning"  # warning | error
