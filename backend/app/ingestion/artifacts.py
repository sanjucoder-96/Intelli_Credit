"""Derived-artifact conventions: deterministic keys + gzipped JSON blobs.

Pipeline stages exchange intermediate results (page texts, OCR lines,
tables, chunks, extraction output) through the StoragePort at deterministic
keys — state stays out of Celery messages and re-runs overwrite in place,
which is what makes stages idempotent (ARCHITECTURE §5 "references, not
blobs" applied to the pipeline).
"""

from __future__ import annotations

import gzip
import json
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from app.application.ports import StoragePort
from app.core.errors import NotFoundError


class PageRecord(BaseModel):
    """Per-page state accumulated by text-layer + OCR stages."""

    page: int
    text: str = ""
    density: float = 0.0
    needs_ocr: bool = False
    ocr_engine: str | None = None  # text_layer | rapidocr | gemini_flash
    ocr_mean_confidence: float | None = None
    image_key: str | None = None  # rendered PNG (scanned pages only)


class PagesArtifact(BaseModel):
    """The ``pages`` artifact: everything downstream stages need per page."""

    pages: list[PageRecord] = Field(default_factory=list)

    def full_text(self) -> list[str]:
        return [record.text for record in self.pages]


class TablesArtifact(BaseModel):
    """Extracted table grids per page."""

    tables: list[tuple[int, list[list[str]]]] = Field(default_factory=list)


class ChunkRecord(BaseModel):
    """One chunk staged for embedding + indexing."""

    text: str
    chunk_kind: str = "prose"
    page_start: int | None = None
    page_end: int | None = None
    section_path: list[str] | None = None


class ChunksArtifact(BaseModel):
    """The ``chunks`` artifact consumed by embed_index."""

    chunks: list[ChunkRecord] = Field(default_factory=list)


def pages_key(document_id: UUID) -> str:
    return f"derived/{document_id}/pages.json.gz"


def tables_key(document_id: UUID) -> str:
    return f"derived/{document_id}/tables.json.gz"


def chunks_key(document_id: UUID) -> str:
    return f"derived/{document_id}/chunks.json.gz"


def extraction_key(document_id: UUID) -> str:
    return f"derived/{document_id}/extraction.json.gz"


def page_image_key(document_id: UUID, page: int) -> str:
    return f"derived/{document_id}/pages/{page}.png"


async def write_json(storage: StoragePort, key: str, payload: Any) -> None:
    """Store a JSON-serializable payload gzipped at ``key``."""
    raw = json.dumps(payload, default=str).encode("utf-8")
    await storage.put_derived(key=key, data=gzip.compress(raw))


async def read_json(storage: StoragePort, key: str) -> Any:
    """Read a gzipped JSON artifact; raises NotFoundError if absent."""
    return json.loads(gzip.decompress(await storage.get(key)))


async def read_json_or_none(storage: StoragePort, key: str) -> Any | None:
    try:
        return await read_json(storage, key)
    except NotFoundError:
        return None
