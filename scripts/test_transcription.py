#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

from app.core.config import settings
from app.services.transcription_service import TranscriptionError, WhisperTranscriptionClient
from app.storage import LocalStorageBackend


def build_client() -> WhisperTranscriptionClient:
    if not settings.whisper_server_ip:
        raise RuntimeError("WHISPER_SERVER_IP is not configured")

    port = settings.whisper_server_port or 80
    base_url = f"http://{settings.whisper_server_ip}:{port}"
    return WhisperTranscriptionClient(
        base_url=base_url,
        request_timeout=settings.whisper_request_timeout_seconds,
        poll_interval=settings.whisper_poll_interval_seconds,
        poll_timeout=settings.whisper_poll_timeout_seconds,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a local M4A file to the Whisper server and print the transcript.")
    parser.add_argument("file", type=Path, help="Path to the local .m4a file to transcribe")
    args = parser.parse_args()

    audio_path: Path = args.file
    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        return 1

    client = build_client()
    storage = LocalStorageBackend(settings.storage_root)
    temp_key = f"tmp/transcription_test_{uuid.uuid4()}{audio_path.suffix or '.m4a'}"

    with audio_path.open("rb") as audio_stream:
        storage.store_file(temp_key, audio_stream, mime_type="audio/mp4")

    try:
        transcript = client.transcribe(storage, temp_key)
    except TranscriptionError as exc:
        print(f"Transcription failed: {exc}", file=sys.stderr)
        return 2
    finally:
        storage.delete_file(temp_key)

    print("Transcript:")
    print(transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
