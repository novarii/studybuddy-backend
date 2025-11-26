from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional


@dataclass
class StoredFileMeta:
    """Metadata returned after storing a file."""

    storage_key: str
    size_bytes: int
    mime_type: Optional[str] = None


class StorageBackend:
    """Interface describing how binary assets are persisted."""

    def store_file(self, storage_key: str, file_obj: BinaryIO, mime_type: str | None = None) -> StoredFileMeta:  # noqa: D401,E501
        raise NotImplementedError

    def open_file(self, storage_key: str) -> BinaryIO:
        """Return a readable binary stream for the stored object."""
        raise NotImplementedError

    def get_file_url(self, storage_key: str) -> str:
        """Return a logical URL that can later be swapped for a pre-signed URL."""
        raise NotImplementedError

    def delete_file(self, storage_key: str) -> None:
        raise NotImplementedError


class LocalStorageBackend(StorageBackend):
    """Persist files on local disk using logical storage keys."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def store_file(self, storage_key: str, file_obj: BinaryIO, mime_type: str | None = None) -> StoredFileMeta:
        target_path = self._resolve_path(storage_key)
        target_path.parent.mkdir(parents=True, exist_ok=True)

        size = 0
        if hasattr(file_obj, "seek"):
            file_obj.seek(0)
        with open(target_path, "wb") as dst:
            while True:
                chunk = file_obj.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                size += len(chunk)

        return StoredFileMeta(storage_key=storage_key, size_bytes=size, mime_type=mime_type)

    def open_file(self, storage_key: str) -> BinaryIO:
        return open(self._resolve_path(storage_key), "rb")

    def get_file_url(self, storage_key: str) -> str:
        return f"/storage/{storage_key}"

    def delete_file(self, storage_key: str) -> None:
        path = self._resolve_path(storage_key)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def _resolve_path(self, storage_key: str) -> Path:
        clean_key = storage_key.lstrip("/")
        return self.root.joinpath(clean_key)
