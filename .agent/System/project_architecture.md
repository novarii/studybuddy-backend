# Project Architecture

## Overview
StudyBuddy Backend is a FastAPI service that orchestrates Panopto lecture audio downloads, transcription, and PDF document uploads for AI-powered study workflows. PostgreSQL stores all metadata, while binary assets live behind a swappable storage abstraction (local disk currently). Route handlers in `app/main.py` stay thin by delegating to service modules.

## Tech Stack
- **API**: FastAPI (`app/main.py`) with dependencies/helpers in `app/api/` and Pydantic schemas in `app/schemas/`.
- **Auth**: Clerk session tokens are verified in `app/api/auth.py`; every route depends on `require_user` to resolve the signed-in UUID.
- **Database**: PostgreSQL accessed through SQLAlchemy ORM (`app/database/db.py`, `app/database/models.py`).
- **Storage abstraction**: `app/storage/__init__.py` exposes `StorageBackend` and `LocalStorageBackend` for filesystem persistence (default root `storage/`).
- **Download/transcription pipeline**: `app/services/downloaders/downloader.py` defines downloader/extractor interfaces, `app/services/downloaders/panopto_downloader.py` wraps the PanoptoDownloader package, and `app/services/transcription_service.py` integrates with a remote Whisper FastAPI server.
- **Services**: `app/services/lectures_service.py`, `app/services/documents_service.py`, and `app/services/users_service.py` handle business logic, deduplication, orchestration, and cleanup.
- **Config**: `app/core/config.py` centralizes environment-driven settings (database URL, storage roots/prefixes, Clerk, Whisper server, etc.).
- **Migrations**: SQL files under `migrations/versions/` contain schema setup. `scripts/run_migrations.sh` pipes them into the Dockerized Postgres instance in order for quick resets.

## Project Structure
```
app/
  adapters/                   # Protocol adapters (Agno-to-Vercel stream conversion)
  agents/                     # Agno-based AI agents (chat, PDF description, knowledge builder)
  api/                        # Auth helpers and future API-specific utilities
  core/                       # Settings + shared utilities
  database/                   # SQLAlchemy session + models
  mcp/                        # MCP server exposing knowledge retrieval as tools
  schemas/                    # Pydantic request/response models
  services/
    documents_service.py      # PDF upload + dedup + user linking
    lectures_service.py       # Lecture ingestion, Panopto + Whisper pipelines
    users_service.py          # Lazy user row creation
    document_chunk_pipeline.py # Slide extraction, AI description, vector ingestion
    lecture_chunk_pipeline.py  # Transcript chunking and vector ingestion
    downloaders/              # Download + extraction interfaces/adapters
    transcription_service.py  # Whisper transcription client/result structs
  storage/                    # StorageBackend interface + local implementation
  main.py                     # FastAPI app + routes
migrations/                   # SQL schema migrations
scripts/                      # Operational helpers (migrations, transcription tests)
storage/                      # Local asset roots (documents/, audio_tmp/, transcripts/)
```

## Core Flows
### Lecture Download & Transcription
1. Client POSTs to `/api/lectures/download` with `course_id`, `panopto_url` (viewer link), `stream_url` (direct podcast URL), optional `title` (`app/main.py`).
2. `LecturesService.request_download(...)` checks `(course_id, panopto_session_id)` for duplicates, links the user, and enqueues `_run_download_pipeline` via FastAPI `BackgroundTasks`.
3. Pipeline (`app/lectures_service.py`):
   - Marks lecture as `downloading`.
   - Uses `PanoptoPackageDownloader` to stream the direct podcast URL into temporary storage key `audio_tmp/{lecture_id}_source.mp4` via `StorageBackend`.
   - Converts to audio with `FFmpegAudioExtractor`, writing `audio/{lecture_id}.m4a` via storage.
   - If Whisper configuration is present, `WhisperTranscriptionClient` uploads the audio to the remote FastAPI server (`/transcribe`), polls `/result/{task_id}`, and returns structured text + segments + VTT metadata. The full payload is stored at `transcripts/{lecture_id}.json` (and `transcripts/{lecture_id}.vtt` when provided).
   - Updates `duration_seconds`, keeps audio/transcript keys, deletes temporary video file, and marks status `completed`.
   - Failures delete the lecture row entirely and remove any stored assets to avoid stale data.
4. Metadata is accessible via `GET /api/lectures/{id}` and `GET /api/lectures/{id}/status`, which verify user linkage.

### Document Upload
1. Client POSTs `/api/documents/upload` with multipart PDF `file`, `course_id`, `user_id` (`app/main.py`).
2. Handler enforces PDF MIME/extension and reads bytes.
3. `DocumentsService.upload_document(...)` computes SHA256 checksum, ensures the owning user exists, checks for duplicates scoped to that user & course, writes bytes to `documents/{document_id}.pdf`, and stores metadata with `owner_id`.
4. When a new document is created, FastAPI schedules `DocumentChunkPipeline.process_document(...)` as a background task. The pipeline renders/deduplicates slides, runs the Gemini description agent, writes a JSON artifact under `storage/document_chunks/{document_id}.json`, and calls `Knowledge.add_content` for each chunk so PgVector tables stay in sync. Missing Voyage credentials cause ingestion to be skipped but do not fail the upload.
5. `GET /api/documents/{id}` returns metadata while hiding storage paths/checksums.
6. `GET /api/documents/{id}/file` streams the stored PDF, still verifying user association.

Deleting a document removes both the metadata row and the physical file because each record now belongs to exactly one user.
The delete endpoint now also calls `DocumentChunkPipeline.cleanup_document(...)` so the stored chunk JSON and related PgVector rows (`slide_chunks_knowledge`) are purged.

### User Linking & Cleanup
- Lecture access remains in `user_lectures`; deleting the final link destroys the lecture and attached audio assets.
- Documents are truly single-owner; `documents.owner_id` holds the FK and deleting the doc removes the file immediately.

## Background Tasks & External Dependencies
- **Panopto downloads**: `requests` library pulls video bytes over HTTP. Actual Panopto auth/tokenization must be supplied by upstream callers.
- **Audio extraction**: `ffmpeg`/`ffprobe` binaries must be available on the host; extractor falls back to copying the video file if `ffmpeg` is missing (still flagged as success but without transcoding guarantees).
- **Whisper transcription**: `WHISPER_SERVER_IP` and `WHISPER_SERVER_PORT` must point to the remote Whisper FastAPI server reachable over the network. Timeouts/poll intervals are tunable via `WHISPER_*` env vars. `scripts/test_transcription.py` can push a local audio file through the same client for quick diagnostics.
- **Storage migration**: All stored paths use logical `storage_key` strings (`documents/...`, `audio/...`, `transcripts/...`) so swapping `LocalStorageBackend` for S3/Spaces only requires implementing `StorageBackend`.

## Configuration & Environment
- `DATABASE_URL` (default `postgresql://postgres:postgres@localhost:5432/studybuddy`)
- `STORAGE_ROOT` (default `storage/` inside repo)
- `DOCUMENTS_STORAGE_PREFIX`, `AUDIO_TEMP_STORAGE_PREFIX` (override folder names if needed)
- `CLERK_SECRET_KEY`, `CLERK_AUTHORIZED_PARTIES` (auth)
- `WHISPER_SERVER_IP`, `WHISPER_SERVER_PORT`, `WHISPER_REQUEST_TIMEOUT_SECONDS`, `WHISPER_POLL_INTERVAL_SECONDS`, `WHISPER_POLL_TIMEOUT_SECONDS` (remote transcription client)
- `VOYAGE_API_KEY`, `VOYAGE_MODEL_ID`, `VOYAGE_EMBED_DIMENSIONS` (Voyage embedder used by Agno Knowledge).
- `KNOWLEDGE_VECTOR_SCHEMA`, `SLIDE_KNOWLEDGE_TABLE`, `LECTURE_KNOWLEDGE_TABLE` (PgVector schema/table settings for slide/lecture knowledge stores).
- Ensure directories `storage/documents/`, `storage/audio_tmp/`, and `storage/transcripts/` are writable (they are created lazily as needed).

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
| DELETE | `/api/documents/{document_id}` | Delete the owner's document (admins can remove any document). |
| POST | `/api/agent/chat` | **Streaming chat endpoint** compatible with Vercel AI SDK v5 (SSE). |
| GET | `/api/dev/lectures` | Dev-only listing of lectures (requires `DEV_ROUTES_ENABLED=true`). |

All routes require a valid Clerk session token in the `Authorization` header or `__session` cookie; `app/auth.py` resolves the UUID used for authorization checks, so clients never submit `user_id` explicitly. Admin overrides are controlled by `ADMIN_USER_IDS` in configuration.

## Chat Agent & Streaming Architecture

The chat feature uses Agno agents with a custom RAG retriever and streams responses via a Vercel AI SDK v5-compatible adapter.

### Components
- **`app/agents/chat_agent.py`**: Creates the `StudyBuddyChatAgent` with a `custom_retriever` that searches both slide and lecture knowledge bases.
- **`app/agents/knowledge_builder.py`**: Factory for PgVector-backed `Knowledge` instances with Voyage AI embeddings.
- **`app/adapters/vercel_stream.py`**: `AgnoVercelAdapter` class that converts Agno `RunEvent` stream to Vercel AI SDK v5 SSE format.

### Stream Format (Vercel AI SDK v5)
The `/api/agent/chat` endpoint returns Server-Sent Events with header `x-vercel-ai-ui-message-stream: v1`:
```
data: {"type":"start","messageId":"..."}
data: {"type":"source-document","sourceId":"...","mediaType":"slide","title":"..."}
data: {"type":"data-rag-source","data":{...full metadata...}}
data: {"type":"text-start","id":"..."}
data: {"type":"text-delta","id":"...","delta":"Hello..."}
data: {"type":"text-end","id":"..."}
data: {"type":"finish"}
data: [DONE]
```

### RAG Source Metadata
Sources include full metadata for frontend deep-linking:
- **Slides**: `document_id`, `slide_number`, `course_id`, `owner_id`
- **Lectures**: `lecture_id`, `start_seconds`, `end_seconds`, `course_id`

### Event Mapping (Agno → Vercel)
| Agno Event | Vercel SSE Type |
|------------|-----------------|
| `RunStartedEvent` | `start` |
| `RunContentEvent` | `text-delta` |
| `RunCompletedEvent` | `text-end`, `finish` |
| `RunErrorEvent` | `error` |
| `ReasoningStepEvent` | `reasoning-delta` |
| `ToolCallStartedEvent` | `tool-input-start` |
| `ToolCallCompletedEvent` | `tool-input-available`, `tool-output-available` |

See [Tasks/frontend_chat_integration.md](../Tasks/frontend_chat_integration.md) for frontend implementation details.

## Related Docs
- [Database Schema](database_schema.md)
- [Repository Index](../README.md)
