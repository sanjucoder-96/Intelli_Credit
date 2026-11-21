"""Blob-storage adapters behind the StoragePort (ADR-0016)."""

from app.infrastructure.storage.local import LocalFilesystemStorage

__all__ = ["LocalFilesystemStorage"]
