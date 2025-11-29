from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import requests

from ..storage import StorageBackend


class TranscriptionError(RuntimeError):
    """Raised when the Whisper server cannot return a transcript."""


class WhisperTranscriptionClient:
    """Client that uploads lecture audio to the Whisper server and polls for transcripts."""

    def __init__(
        self,
        base_url: str,
        *,
        request_timeout: int = 30,
        poll_interval: int = 5,
        poll_timeout: int = 600,
    ) -> None:
        if not base_url:
            raise ValueError("Whisper server base URL must be provided")
        self.base_url = base_url.rstrip("/")
        self.request_timeout = request_timeout
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout

    def transcribe(self, storage: StorageBackend, audio_storage_key: str) -> str:
        """Upload audio referenced by storage_key and block until the transcript is ready."""

        task_id = self._submit_transcription_job(storage, audio_storage_key)
        return self._wait_for_transcription(task_id)

    def _submit_transcription_job(self, storage: StorageBackend, audio_storage_key: str) -> str:
        url = f"{self.base_url}/transcribe"
        try:
            with storage.open_file(audio_storage_key) as audio_stream:
                filename = Path(audio_storage_key).name or "audio.m4a"
                response = requests.post(
                    url,
                    files={"audio": (filename, audio_stream, "audio/mp4")},
                    timeout=self.request_timeout,
                )
        except requests.RequestException as exc:  # type: ignore[attr-defined]
            raise TranscriptionError(f"Whisper server submission failed: {exc}") from exc
        except OSError as exc:
            raise TranscriptionError(f"Unable to read audio file for transcription: {exc}") from exc

        try:
            response.raise_for_status()
            payload = response.json()
        except ValueError as exc:
            raise TranscriptionError("Whisper server returned invalid JSON for submission") from exc

        task_id = payload.get("task_id")
        if not task_id or not isinstance(task_id, str):
            raise TranscriptionError("Whisper server response missing task_id")
        return task_id

    def _wait_for_transcription(self, task_id: str) -> str:
        url = f"{self.base_url}/result/{task_id}"
        deadline = time.monotonic() + self.poll_timeout

        while time.monotonic() < deadline:
            try:
                response = requests.get(url, timeout=self.request_timeout)
                response.raise_for_status()
                payload = response.json()
            except requests.RequestException as exc:  # type: ignore[attr-defined]
                raise TranscriptionError(f"Failed to fetch transcription status: {exc}") from exc
            except ValueError as exc:
                raise TranscriptionError("Whisper server returned invalid JSON for status request") from exc

            status = str(payload.get("status") or "").lower()
            if status in {"completed", "success", "succeeded", "done", "complete"}:
                transcript_text = payload.get("transcript") or payload.get("text") or payload.get("result")
                if not transcript_text or not isinstance(transcript_text, str):
                    raise TranscriptionError("Whisper server completed without returning a transcript")
                return transcript_text

            if status in {"failed", "error"}:
                error_message = payload.get("error") or "Transcription failed"
                raise TranscriptionError(str(error_message))

            if status in {"pending", "processing", "queued", "running"}:
                time.sleep(self.poll_interval)
                continue

            # Unknown status - wait briefly before trying again.
            time.sleep(self.poll_interval)

        raise TranscriptionError("Timed out waiting for Whisper transcription result")
