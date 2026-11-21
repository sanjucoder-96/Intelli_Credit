"""Table extraction (ADR-0006): pdfplumber for born-digital pages.

Scanned-table reconstruction via Gemini vision hangs off the same seam when
an API key is configured; classical reconstruction of scanned tables is
deliberately not attempted (unreliable — the ADR's rejected alternative).
"""

from __future__ import annotations

import io

from app.core.logging import get_logger

logger = get_logger("app.ingestion.tables")


def extract_tables_born_digital(
    content: bytes, pages_with_text: list[int]
) -> list[tuple[int, list[list[str]]]]:
    """pdfplumber table grids for the given (1-based) born-digital pages."""
    import pdfplumber

    grids: list[tuple[int, list[list[str]]]] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_number in pages_with_text:
            if page_number - 1 >= len(pdf.pages):
                continue
            page = pdf.pages[page_number - 1]
            try:
                tables = page.extract_tables() or []
            except Exception as exc:  # pdfplumber chokes on exotic geometry
                logger.warning("pdfplumber_page_failed", page=page_number, error=str(exc))
                continue
            for table in tables:
                grid = [["" if cell is None else str(cell) for cell in row] for row in table]
                if grid and any(any(cell.strip() for cell in row) for row in grid):
                    grids.append((page_number, grid))
    return grids
