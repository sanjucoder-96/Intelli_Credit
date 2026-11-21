"""PDF primitives over pypdfium2: text layer, density scoring, rendering.

Per-page decisions (ARCHITECTURE §7): Indian credit files are hybrid PDFs —
typed annexures and scanned stamped certificates in one file — so
``needs_ocr`` is scored page by page, never per document.
"""

from __future__ import annotations

import io
from dataclasses import dataclass


class PdfPasswordRequiredError(Exception):
    """The PDF is encrypted; the UI must collect a password (§7)."""


@dataclass(frozen=True)
class PageText:
    """Text-layer read of one page."""

    page: int
    text: str
    density: float  # chars per square inch of page area


def _open(content: bytes, password: str | None):  # type: ignore[no-untyped-def]
    import pypdfium2 as pdfium

    try:
        return pdfium.PdfDocument(io.BytesIO(content), password=password)
    except Exception as exc:  # pypdfium2 raises PdfiumError on bad password
        if "password" in str(exc).lower() or "PDF_ERR_PASSWORD" in str(exc):
            raise PdfPasswordRequiredError from exc
        raise


def is_encrypted_without_password(content: bytes) -> bool:
    """Whether the PDF refuses to open without a password."""
    try:
        pdf = _open(content, None)
    except PdfPasswordRequiredError:
        return True
    pdf.close()
    return False


def extract_text_layer(
    content: bytes, *, password: str | None = None, density_threshold: float
) -> list[PageText]:
    """Per-page text + density; density below threshold marks OCR candidates."""
    pdf = _open(content, password)
    try:
        pages: list[PageText] = []
        for index in range(len(pdf)):
            page = pdf[index]
            width, height = page.get_size()  # points (1/72 inch)
            area_sq_inch = max((width / 72.0) * (height / 72.0), 1.0)
            textpage = page.get_textpage()
            text = textpage.get_text_bounded() or ""
            textpage.close()
            density = len(text.strip()) / area_sq_inch
            pages.append(PageText(page=index + 1, text=text, density=density))
        return pages
    finally:
        pdf.close()


def render_page_png(
    content: bytes, page_number: int, *, dpi: int, password: str | None = None
) -> bytes:
    """Render one page (1-based) to PNG bytes at ``dpi``."""
    pdf = _open(content, password)
    try:
        page = pdf[page_number - 1]
        bitmap = page.render(scale=dpi / 72.0)
        image = bitmap.to_pil()
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        pdf.close()
