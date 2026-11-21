"""Local-filesystem StoragePort backend (ADR-0016).

Content-addressed layout (dedup within a case, per the ADR):

    cases/{case_id}/{sha256}{ext}     uploaded originals
    orphan/{sha256}{ext}              uploads with no case yet
    {explicit key}                    pipeline-derived artifacts (page images,
                                      extracted text, web-evidence snapshots)

Writes are atomic (temp file + rename) so a killed worker never leaves a
half-written blob at a final key. Paths never leak upward — callers hold
opaque keys only. An S3/MinIO adapter replaces this class behind the same
port when multi-host arrives; the antivirus scan hook wraps ``put`` (the
Phase-4 guard is size/extension validation at the API boundary; a real AV
engine is an ops integration documented in the threat model, never faked).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
import tempfile
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from app.application.ports import StoredBlob
from app.core.errors import NotFoundError, ValidationFailedError

_SAFE_KEY = re.compile(r"^[a-zA-Z0-9._/\-]+$")


class LocalFilesystemStorage:
    """StoragePort adapter over a root directory (a Docker volume in compose)."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        """Resolve a storage key to a filesystem path, refusing traversal."""
        if not _SAFE_KEY.match(key) or ".." in PurePosixPath(key).parts:
            raise ValidationFailedError(f"Illegal storage key: {key!r}")
        return self._root / PurePosixPath(key)

    @staticmethod
    def _extension(filename: str) -> str:
        suffix = Path(filename).suffix.lower()
        return suffix if len(suffix) <= 16 and re.fullmatch(r"\.[a-z0-9]+", suffix or ".x") else ""

    async def put(self, *, case_id: UUID | None, filename: str, data: bytes) -> StoredBlob:
        """Store an uploaded original, content-addressed within its case."""
        sha256 = hashlib.sha256(data).hexdigest()
        scope = f"cases/{case_id}" if case_id is not None else "orphan"
        key = f"{scope}/{sha256}{self._extension(filename)}"
        # Content-addressed: an existing path already holds these exact bytes.
        await self._write(key, data, overwrite=False)
        return StoredBlob(key=key, sha256=sha256, size=len(data))

    async def put_derived(self, *, key: str, data: bytes) -> StoredBlob:
        """Store a derived artifact at an explicit deterministic key.

        Derived keys are REUSED with changing content as pipeline stages
        refine them (pages → +OCR text), so this always overwrites — the
        atomic replace keeps readers consistent.
        """
        sha256 = hashlib.sha256(data).hexdigest()
        await self._write(key, data, overwrite=True)
        return StoredBlob(key=key, sha256=sha256, size=len(data))

    async def get(self, key: str) -> bytes:
        path = self._path_for(key)

        def _read() -> bytes:
            try:
                return path.read_bytes()
            except FileNotFoundError as exc:
                raise NotFoundError(f"No blob at key {key!r}") from exc

        return await asyncio.to_thread(_read)

    async def exists(self, key: str) -> bool:
        path = self._path_for(key)
        return await asyncio.to_thread(path.is_file)

    async def _write(self, key: str, data: bytes, *, overwrite: bool) -> None:
        """Atomic write: temp file in the destination directory, then rename."""
        path = self._path_for(key)

        def _sync_write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            if not overwrite and path.exists():
                return
            fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".tmp-{uuid4().hex[:8]}-")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                os.replace(tmp_name, path)
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)

        await asyncio.to_thread(_sync_write)
