"""Chunking profiles (ARCHITECTURE §8): layout-aware, tables never split mid-row.

Token counts are estimated at ~4 characters/token — chunk boundaries are a
retrieval tuning knob, not an exact science; the estimate keeps chunks inside
embedding-model limits with margin.
"""

from __future__ import annotations

import re

from app.core.config import IngestConfig
from app.docintel.registry import ChunkingProfile
from app.ingestion.artifacts import ChunkRecord, PagesArtifact

_CHARS_PER_TOKEN = 4
_HEADING = re.compile(r"^\s*(\d+(\.\d+)*[.)]?\s+\S|[A-Z][A-Z &/-]{6,})")


def chunk_pages(
    pages: PagesArtifact,
    tables: list[tuple[int, list[list[str]]]],
    profile: ChunkingProfile,
    config: IngestConfig,
) -> list[ChunkRecord]:
    """Chunk a document's pages + tables under its type's profile."""
    if profile is ChunkingProfile.KV:
        return _page_chunks(pages)
    chunks = _prose_chunks(pages, config)
    chunks.extend(_table_chunks(tables, config))
    return chunks


def _page_chunks(pages: PagesArtifact) -> list[ChunkRecord]:
    """KV forms (GSTR, ITR acknowledgements): one chunk per page."""
    return [
        ChunkRecord(
            text=record.text.strip(), chunk_kind="kv", page_start=record.page, page_end=record.page
        )
        for record in pages.pages
        if record.text.strip()
    ]


def _prose_chunks(pages: PagesArtifact, config: IngestConfig) -> list[ChunkRecord]:
    """Heading-anchored greedy packing with overlap, page spans retained."""
    target_chars = config.chunk_target_tokens * _CHARS_PER_TOKEN
    overlap_chars = int(target_chars * config.chunk_overlap_ratio)

    # (page, paragraph) units in reading order.
    units: list[tuple[int, str]] = []
    for record in pages.pages:
        for paragraph in re.split(r"\n\s*\n", record.text):
            paragraph = paragraph.strip()
            if paragraph:
                units.append((record.page, paragraph))

    chunks: list[ChunkRecord] = []
    buffer: list[tuple[int, str]] = []
    size = 0

    def flush() -> None:
        nonlocal buffer, size
        if not buffer:
            return
        text = "\n\n".join(paragraph for _, paragraph in buffer)
        chunks.append(
            ChunkRecord(
                text=text,
                chunk_kind="prose",
                page_start=buffer[0][0],
                page_end=buffer[-1][0],
            )
        )
        # Overlap: carry trailing paragraphs into the next chunk.
        carried: list[tuple[int, str]] = []
        carried_size = 0
        for page, paragraph in reversed(buffer):
            if carried_size + len(paragraph) > overlap_chars:
                break
            carried.insert(0, (page, paragraph))
            carried_size += len(paragraph)
        buffer = carried
        size = carried_size

    for page, paragraph in units:
        starts_section = bool(_HEADING.match(paragraph))
        if buffer and (
            size + len(paragraph) > target_chars or (starts_section and size > target_chars // 2)
        ):
            flush()
        buffer.append((page, paragraph))
        size += len(paragraph)
    flush()
    return chunks


def _table_chunks(
    tables: list[tuple[int, list[list[str]]]], config: IngestConfig
) -> list[ChunkRecord]:
    """Tables become markdown chunks; large tables split by row-groups with
    the header repeated (never mid-row)."""
    target_chars = config.chunk_target_tokens * _CHARS_PER_TOKEN
    chunks: list[ChunkRecord] = []
    for page, grid in tables:
        if not grid:
            continue
        header, *rows = grid if len(grid) > 1 else (grid[0], [])

        def render(rows_slice: list[list[str]], header: list[str] = header) -> str:
            lines = [" | ".join(header), " | ".join("---" for _ in header)]
            lines.extend(" | ".join(cell for cell in row) for row in rows_slice)
            return "\n".join(lines)

        group: list[list[str]] = []
        for row in rows:
            group.append(row)
            if len(render(group)) > target_chars:
                overflow = group.pop() if len(group) > 1 else None
                chunks.append(
                    ChunkRecord(
                        text=render(group), chunk_kind="table", page_start=page, page_end=page
                    )
                )
                group = [overflow] if overflow else []
        if group or not rows:
            chunks.append(
                ChunkRecord(text=render(group), chunk_kind="table", page_start=page, page_end=page)
            )
    return chunks
