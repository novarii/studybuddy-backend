"""Storage abstraction for file persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional


@dataclass
class StoredFileMeta:
    """Metadata returned after storing a file."""

    storage_key: str
    size_bytes: int
    mime_type: Optional[str]


class StorageBackend(ABC):
    """Abstract interface for file storage operations."""

    @abstractmethod
    def store_file(
        self,
        storage_key: str,
        file_obj: BinaryIO,
        *,
        mime_type: Optional[str] = None,
    ) -> StoredFileMeta:
        """Store a file and return its metadata."""
        ...

    @abstractmethod
    def open_file(self, storage_key: str) -> BinaryIO:
        """Open a stored file for reading."""
        ...

    @abstractmethod
    def delete_file(self, storage_key: str) -> None:
        """Delete a stored file."""
        ...


class LocalStorageBackend(StorageBackend):
    """Store files on the local filesystem."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve_path(self, storage_key: str) -> Path:
        """Resolve storage key to filesystem path with path traversal protection."""
        clean_key = storage_key.lstrip("/")
        if ".." in clean_key:
            raise ValueError("Invalid storage key: path traversal not allowed")
        resolved = self.root.joinpath(clean_key).resolve()
        if not resolved.is_relative_to(self.root):
            raise ValueError("Invalid storage key: path traversal detected")
        return resolved

    def store_file(
        self,
        storage_key: str,
        file_obj: BinaryIO,
        *,
        mime_type: Optional[str] = None,
    ) -> StoredFileMeta:
        """Store a file and return its metadata."""
        path = self._resolve_path(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)

        size = 0
        with open(path, "wb") as dest:
            while chunk := file_obj.read(1024 * 1024):
                dest.write(chunk)
                size += len(chunk)

        return StoredFileMeta(
            storage_key=storage_key,
            size_bytes=size,
            mime_type=mime_type,
        )

    def open_file(self, storage_key: str) -> BinaryIO:
        """Open a stored file for reading."""
        path = self._resolve_path(storage_key)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {storage_key}")
        return open(path, "rb")

    def delete_file(self, storage_key: str) -> None:
        """Delete a stored file. No error if file doesn't exist."""
        path = self._resolve_path(storage_key)
        path.unlink(missing_ok=True)


__all__ = ["StorageBackend", "LocalStorageBackend", "StoredFileMeta"]
