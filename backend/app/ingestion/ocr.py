"""OCR escalation chain (ADR-0006): RapidOCR primary, Gemini vision fallback.

RapidOCR (PP-OCRv4 on ONNX Runtime, models bundled in the wheel — fully
offline) yields line-level text + confidence + geometry: the load-bearing
provenance for citation pins. Pages whose mean line confidence falls below
the configured threshold escalate to Gemini Flash vision (page-level
citations only — honest granularity per the ADR); numeric trust is enforced
later by the extractors' deterministic post-validators, never assumed.
"""

from __future__ import annotations

import threading
from typing import Any

from pydantic import BaseModel, Field

from app.core.logging import get_logger
from app.infrastructure.gemini import GeminiCallResult, GeminiClient

logger = get_logger("app.ingestion.ocr")

_rapidocr_engine: Any = None
_rapidocr_lock = threading.Lock()


class OcrLine(BaseModel):
    """One recognized line with normalized geometry."""

    text: str
    confidence: float
    bbox: tuple[float, float, float, float] | None = None  # normalized x0,y0,x1,y1


class OcrPageResult(BaseModel):
    """OCR output for one page."""

    lines: list[OcrLine] = Field(default_factory=list)
    engine: str = "rapidocr"

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(line.confidence for line in self.lines) / len(self.lines)


class VisionPageText(BaseModel):
    """Gemini vision transcription schema (page-level provenance)."""

    lines: list[str] = Field(default_factory=list)


def _engine() -> Any:
    global _rapidocr_engine
    if _rapidocr_engine is None:
        with _rapidocr_lock:
            if _rapidocr_engine is None:
                from rapidocr_onnxruntime import RapidOCR

                logger.info("loading_rapidocr_engine")
                _rapidocr_engine = RapidOCR()
    return _rapidocr_engine


def rapidocr_page(png: bytes, *, image_width: int = 0, image_height: int = 0) -> OcrPageResult:
    """Run RapidOCR on one rendered page image."""
    result, _elapsed = _engine()(png)
    lines: list[OcrLine] = []
    if result:
        for entry in result:
            box, text, confidence = entry[0], str(entry[1]), float(entry[2])
            bbox = None
            if box and image_width and image_height:
                xs = [point[0] for point in box]
                ys = [point[1] for point in box]
                bbox = (
                    min(xs) / image_width,
                    min(ys) / image_height,
                    max(xs) / image_width,
                    max(ys) / image_height,
                )
            lines.append(OcrLine(text=text, confidence=confidence, bbox=bbox))
    return OcrPageResult(lines=lines, engine="rapidocr")


async def gemini_vision_page(
    client: GeminiClient, png: bytes
) -> tuple[OcrPageResult, GeminiCallResult]:
    """Escalation tier: transcribe a page with Gemini Flash vision.

    Returns page-level provenance (no line geometry — the honest granularity
    for LLM OCR, ADR-0006) with confidence left at the escalation default.
    """
    transcription, call = await client.generate_structured(
        schema=VisionPageText,
        prompt=(
            "Transcribe every line of text visible on this scanned document page, in "
            "reading order. Preserve numbers exactly as printed. Output one entry per line."
        ),
        image_png=png,
    )
    lines = [OcrLine(text=line, confidence=0.85, bbox=None) for line in transcription.lines]
    return OcrPageResult(lines=lines, engine="gemini_flash"), call
