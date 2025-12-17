# StudyBuddy Backend

FastAPI service providing Panopto lecture ingestion and PDF document uploads for the StudyBuddy platform.

## Getting Started

1. **Install dependencies**
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment**
   - `DATABASE_URL` (default: `postgresql://postgres:postgres@localhost:5432/studybuddy`)
   - `STORAGE_ROOT` (default: `storage/` in the repo)
   - Optional overrides: `DOCUMENTS_STORAGE_PREFIX`, `AUDIO_TEMP_STORAGE_PREFIX`.
   - `WHISPER_SERVER_IP` / `WHISPER_SERVER_PORT` so the lecture transcription pipeline can reach your Whisper FastAPI server (poll timing tunables available via `WHISPER_REQUEST_TIMEOUT_SECONDS`, `WHISPER_POLL_INTERVAL_SECONDS`, `WHISPER_POLL_TIMEOUT_SECONDS`).
   - `ffmpeg`/`ffprobe` binaries must be on `PATH` for audio extraction.

3. **Run migrations**
   ```bash
   docker compose up -d db
   ./scripts/run_migrations.sh
   ```
   The helper script applies every SQL migration under `migrations/versions/` in order so the schema always matches the latest code.

4. **Start the API**
   ```bash
   export CORS_ALLOW_ORIGINS="https://rochester.hosted.panopto.com"
   uvicorn app.main:app --reload
   ```

## Architecture Notes

- HTTP routes live in `app/main.py` and proxy to the service layer under `app/services/` (for example `app/services/lectures_service.py` and `app/services/documents_service.py`).
- SQLAlchemy models and enums are defined in `app/database/models.py`; database access utilities live beside them in `app/database/db.py`.
- File persistence flows through the storage abstraction provided by `app/storage/`. Local disk storage is the default implementation and keeps files under `storage/documents/` and `storage/audio_tmp/`.
- The Panopto download pipeline is orchestrated by `LecturesService` using `PanoptoPackageDownloader` (adapter around the PanoptoDownloader PyPI package) and `FFmpegAudioExtractor`. Audio files persist temporarily (logical keys `audio/{lecture_id}.m4a`).
- Successful runs store Whisper transcription payloads (text + segments + VTT) as JSON files under `storage/transcripts/{lecture_id}.json` so downstream jobs can reuse the metadata.
- Document uploads compute SHA256 checksums to deduplicate **per user per course** (`documents.owner_id`), so each user controls their own uploads.
