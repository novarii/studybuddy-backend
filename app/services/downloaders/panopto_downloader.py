from __future__ import annotations

import logging
import tempfile
import uuid
from pathlib import Path
from typing import Callable

from PanoptoDownloader import download as panopto_download
from PanoptoDownloader import exceptions as panopto_exceptions

from .downloader import DownloadError, DownloadResult, PanoptoDownloader as DownloaderInterface
from ...storage import StorageBackend

logger = logging.getLogger(__name__)


class PanoptoPackageDownloader(DownloaderInterface):
    """Panopto downloader powered by the external PanoptoDownloader package."""

    def __init__(self, progress_callback: Callable[[int], None] | None = None) -> None:
        self.progress_callback = progress_callback or (lambda _progress: None)

    def download_video(self, panopto_url: str, storage: StorageBackend, destination_key: str) -> DownloadResult:
        temp_path = self._build_temp_path()
        try:
            panopto_download(panopto_url, str(temp_path), self.progress_callback)
        except (
            panopto_exceptions.RegexNotMatch,
            panopto_exceptions.NotExist,
            panopto_exceptions.NotSupported,
            panopto_exceptions.NotAVideo,
            panopto_exceptions.NotAFile,
            panopto_exceptions.AlreadyExists,
        ) as exc:
            temp_path.unlink(missing_ok=True)
            raise DownloadError(str(exc)) from exc
        except Exception as exc:  # pragma: no cover - defensive
            temp_path.unlink(missing_ok=True)
            logger.exception("Panopto package download failed")
            raise DownloadError(str(exc)) from exc

        try:
            with open(temp_path, "rb") as payload:
                meta = storage.store_file(destination_key, payload, mime_type="video/mp4")
        finally:
            temp_path.unlink(missing_ok=True)

        return DownloadResult(storage_key=meta.storage_key, size_bytes=meta.size_bytes, mime_type=meta.mime_type)

    def _build_temp_path(self) -> Path:
        temp_dir = Path(tempfile.gettempdir())
        candidate = temp_dir / f"panopto_{uuid.uuid4()}.mp4"
        # Ensure no existing file conflicts before returning
        if candidate.exists():
            candidate.unlink()
        return candidate
