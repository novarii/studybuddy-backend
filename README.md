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
   - `ffmpeg`/`ffprobe` binaries must be on `PATH` for audio extraction.

3. **Run migrations**
   ```bash
   psql "$DATABASE_URL" -f migrations/versions/001_initial.sql
   ```
   The SQL script creates enums, tables, indexes, and timestamp triggers as described in `.agent/Tasks/studybuddy_initial_phase.md`.

4. **Start the API**
   ```bash
   uvicorn app.main:app --reload
   ```

## Architecture Notes

- HTTP routes live in `app/main.py` and proxy to services (`app/lectures_service.py`, `app/documents_service.py`).
- SQLAlchemy models and enums are defined in `app/models.py`; database access uses `app/db.py`.
- File persistence flows through the storage abstraction (`app/storage.py`). Local disk storage is the default implementation and keeps files under `storage/documents/` and `storage/audio_tmp/`.
- The Panopto download pipeline is orchestrated by `LecturesService` using `HttpPanoptoDownloader` and `FFmpegAudioExtractor` from `app/downloader.py`. Audio files persist temporarily (logical keys `audio/{lecture_id}.m4a`).
- Document uploads compute SHA256 checksums to deduplicate per course and share files via `user_documents` links.
