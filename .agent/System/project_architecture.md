# Project Architecture

## Overview
StudyBuddy Backend is a FastAPI service that orchestrates Panopto lecture audio downloads and PDF document uploads for AI-powered study workflows. PostgreSQL stores all metadata, while files live behind a swappable storage abstraction (local disk currently). Route handlers in `app/main.py` stay thin by delegating to domain services.

## Tech Stack
- **API**: FastAPI (`app/main.py`) with Pydantic schemas in `app/schemas.py`.
- **Auth**: Clerk session tokens are verified in `app/auth.py`; every route depends on `require_user` to resolve the signed-in UUID.
- **Database**: PostgreSQL accessed through SQLAlchemy ORM (`app/db.py`, `app/models.py`).
- **Storage abstraction**: `app/storage.py` exposes `StorageBackend` and `LocalStorageBackend` for filesystem persistence.
- **Panopto pipeline**: `app/downloader.py` defines the storage/audio interfaces and `app/panopto_downloader.py` supplies the production downloader powered by the external `PanoptoDownloader` package, drastically improving reliability over raw HTTP fetches.
- **Services**: `app/lectures_service.py` and `app/documents_service.py` handle business logic, deduplication, and orchestration.
- **Config**: `app/config.py` centralizes environment-driven settings (database URL, storage roots/prefixes).
- **Migrations**: SQL files under `migrations/versions/` contain schema setup. `scripts/run_migrations.sh` pipes them into the Dockerized Postgres instance in order for quick resets.

## Project Structure
```
app/
  config.py          # Settings dataclass (DB URL, storage roots)
  db.py              # SQLAlchemy engine/session + Base
  models.py          # ORM models & enums for lectures/documents/link tables
  schemas.py         # Pydantic request/response models
  storage.py         # StorageBackend interface + local implementation
  downloader.py      # Storage + FFmpeg audio extractor interfaces
  panopto_downloader.py # Adapter around PanoptoDownloader PyPI package
  lectures_service.py# Lecture ingestion, background pipeline, cleanup
  documents_service.py# PDF upload + dedup + user linking
  utils.py           # Helpers (e.g., Panopto session ID extraction)
  main.py            # FastAPI app + routes
migrations/versions/001_initial.sql # Schema, enums, triggers
storage/             # Local asset roots (documents/, audio_tmp/)
```

## Core Flows
### Lecture Download
1. Client POSTs to `/api/lectures/download` with `course_id`, `panopto_url` (viewer link), `stream_url` (direct podcast URL), optional `title` (`app/main.py`).
2. `LecturesService.request_download(...)` checks `(course_id, panopto_session_id)` for duplicates, links the user, and enqueues `_run_download_pipeline` via FastAPI `BackgroundTasks`.
3. Pipeline (`app/lectures_service.py`):
   - Marks lecture as `downloading`.
   - Uses `PanoptoPackageDownloader` to stream the direct podcast URL into temporary storage key `audio_tmp/{lecture_id}_source.mp4` via `StorageBackend`.
   - Converts to audio with `FFmpegAudioExtractor`, writing `audio/{lecture_id}.m4a` via storage.
   - Updates `duration_seconds`, keeps audio key for later transcription, deletes temporary video file, and marks status `completed`.
   - On failure, sets `status='failed'`, stores `error_message`, and cleans up temp keys.
4. Metadata is accessible via `GET /api/lectures/{id}` and `GET /api/lectures/{id}/status`, which verify user linkage.

### Document Upload
1. Client POSTs `/api/documents/upload` with multipart PDF `file`, `course_id`, `user_id` (`app/main.py`).
2. Handler enforces PDF MIME/extension and reads bytes.
3. `DocumentsService.upload_document(...)` computes SHA256 checksum, ensures the owning user exists, checks for duplicates scoped to that user & course, writes bytes to `documents/{document_id}.pdf`, and stores metadata with `owner_id`.
4. `GET /api/documents/{id}` returns metadata while hiding storage paths/checksums.
5. `GET /api/documents/{id}/file` streams the stored PDF, still verifying user association.

Deleting a document removes both the metadata row and the physical file because each record now belongs to exactly one user.

### User Linking & Cleanup
- Lecture access remains in `user_lectures`; deleting the final link destroys the lecture and attached audio assets.
- Documents are truly single-owner; `documents.owner_id` holds the FK and deleting the doc removes the file immediately.

## Background Tasks & External Dependencies
- **Panopto downloads**: `requests` library pulls video bytes over HTTP. Actual Panopto auth/tokenization must be supplied by upstream callers.
- **Audio extraction**: `ffmpeg`/`ffprobe` binaries must be available on the host; extractor falls back to copying the video file if `ffmpeg` is missing (still flagged as success but without transcoding guarantees).
- **Storage migration**: All stored paths use logical `storage_key` strings (`audio/...`, `documents/...`) so swapping `LocalStorageBackend` for S3/Spaces only requires implementing `StorageBackend`.

## Configuration & Environment
- `DATABASE_URL` (default `postgresql://postgres:postgres@localhost:5432/studybuddy`)
- `STORAGE_ROOT` (default `storage/` inside repo)
- `DOCUMENTS_STORAGE_PREFIX`, `AUDIO_TEMP_STORAGE_PREFIX` (override folder names if needed)
- Ensure directories `storage/documents/` and `storage/audio_tmp/` exist and are writable.

## API Surface
| Method | Path | Description |
| --- | --- | --- |
| GET | `/api/courses` | Lists courses owned by the authenticated user (now backed by DB rows). |
| POST | `/api/lectures/download` | Create/queue a lecture download job (idempotent per course/session). |
| GET | `/api/lectures/{lecture_id}` | Full lecture metadata for linked user. |
| GET | `/api/lectures/{lecture_id}/status` | Compact status view (lectures). |
| DELETE | `/api/lectures/{lecture_id}` | Remove user association or, for admins, hard-delete the lecture. |
| POST | `/api/documents/upload` | Upload/attach a PDF to a course + user. |
| GET | `/api/documents/{document_id}` | Document metadata (no storage info). |
| GET | `/api/documents/{document_id}/file` | Stream PDF bytes for linked user. |
| DELETE | `/api/documents/{document_id}` | Delete the owner’s document (admins can remove any document). |
| GET | `/api/dev/lectures` | Dev-only listing of lectures (requires `DEV_ROUTES_ENABLED=true`). |

All routes require a valid Clerk session token in the `Authorization` header or `__session` cookie; `app/auth.py` resolves the UUID used for authorization checks, so clients never submit `user_id` explicitly. Admin overrides are controlled by `ADMIN_USER_IDS` in configuration.

## Related Docs
- [Database Schema](database_schema.md)
- [Repository Index](../README.md)
