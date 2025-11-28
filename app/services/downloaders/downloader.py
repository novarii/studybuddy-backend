from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Optional

import requests

from ...storage import StorageBackend

logger = logging.getLogger(__name__)


class DownloadError(RuntimeError):
    """Raised when a Panopto asset could not be downloaded."""


class AudioExtractionError(RuntimeError):
    """Raised when audio could not be extracted from the video file."""


@dataclass
class DownloadResult:
    storage_key: str
    size_bytes: int
    mime_type: Optional[str]


@dataclass
class AudioExtractionResult:
    storage_key: str
    size_bytes: int
    mime_type: Optional[str]
    duration_seconds: Optional[int]


class PanoptoDownloader:
    """Interface responsible for retrieving the raw Panopto asset."""

    def download_video(self, panopto_url: str, storage: StorageBackend, destination_key: str) -> DownloadResult:  # noqa: D401,E501
        raise NotImplementedError


class AudioExtractor:
    """Interface that extracts audio tracks from videos."""

    def extract_audio(self, storage: StorageBackend, video_key: str, audio_key: str) -> AudioExtractionResult:  # noqa: D401,E501
        raise NotImplementedError


class HttpPanoptoDownloader(PanoptoDownloader):
    """Download Panopto content via HTTP GET requests."""

    def __init__(self, timeout: int = 60) -> None:
        self.timeout = timeout

    def download_video(self, panopto_url: str, storage: StorageBackend, destination_key: str) -> DownloadResult:
        try:
            response = requests.get(panopto_url, timeout=self.timeout, stream=True)
            response.raise_for_status()
        except requests.RequestException as exc:  # type: ignore[attr-defined]
            raise DownloadError(str(exc)) from exc

        with tempfile.NamedTemporaryFile(delete=False) as tmp_file:
            temp_path = Path(tmp_file.name)
            size = 0
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                tmp_file.write(chunk)
                size += len(chunk)

        try:
            with open(temp_path, "rb") as payload:
                meta = storage.store_file(destination_key, payload, mime_type=response.headers.get("Content-Type"))
        finally:
            temp_path.unlink(missing_ok=True)

        return DownloadResult(storage_key=meta.storage_key, size_bytes=meta.size_bytes, mime_type=meta.mime_type)


class FFmpegAudioExtractor(AudioExtractor):
    """Use ffmpeg/ffprobe binaries to extract the audio track."""

    def __init__(self, audio_codec: str = "aac", audio_mime_type: str = "audio/mp4") -> None:
        self.audio_codec = audio_codec
        self.audio_mime_type = audio_mime_type

    def extract_audio(self, storage: StorageBackend, video_key: str, audio_key: str) -> AudioExtractionResult:
        with storage.open_file(video_key) as source_stream:
            video_temp = self._materialize_temp_file(source_stream, suffix=Path(video_key).suffix or ".mp4")
        audio_temp = self._build_temp_path(suffix=".m4a")

        try:
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                str(video_temp),
                "-vn",
                "-acodec",
                self.audio_codec,
                str(audio_temp),
            ]
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except FileNotFoundError as exc:
                logger.warning("ffmpeg not available, falling back to passthrough for audio extraction")
                shutil.copyfile(video_temp, audio_temp)
            except subprocess.CalledProcessError as exc:  # pragma: no cover - best effort logging
                raise AudioExtractionError(f"ffmpeg failed: {exc}") from exc

            duration = self._probe_duration(audio_temp)
            with open(audio_temp, "rb") as audio_stream:
                meta = storage.store_file(audio_key, audio_stream, mime_type=self.audio_mime_type)
        finally:
            video_temp.unlink(missing_ok=True)
            audio_temp.unlink(missing_ok=True)

        return AudioExtractionResult(
            storage_key=meta.storage_key,
            size_bytes=meta.size_bytes,
            mime_type=meta.mime_type,
            duration_seconds=duration,
        )

    def _materialize_temp_file(self, stream: BinaryIO, suffix: str) -> Path:
        temp_path = self._build_temp_path(suffix)
        with open(temp_path, "wb") as tmp:
            shutil.copyfileobj(stream, tmp)
        return temp_path

    def _build_temp_path(self, suffix: str) -> Path:
        fd, path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        return Path(path)

    def _probe_duration(self, audio_path: Path) -> Optional[int]:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        except FileNotFoundError:
            logger.warning("ffprobe not available; returning unknown duration")
            return None
        except subprocess.CalledProcessError:
            return None

        try:
            duration_float = float(result.stdout.strip())
        except (TypeError, ValueError):
            return None
        return int(duration_float)
