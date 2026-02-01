# Database Schema

**Status:** Accepted
# Database Schema

Schema is provisioned by the SQL migrations under `migrations/versions/` (001–005 as of now). PostgreSQL is the source of truth for all metadata (files are referenced via logical storage keys only).

## Enumerations
- `lecture_status`: `pending`, `downloading`, `completed`, `failed`.
- `document_status`: `uploaded`, `failed`.

## Tables
### lectures
| Column | Type | Notes |
| --- | --- | --- |
| id | UUID PK | `gen_random_uuid()` default. |
| course_id | UUID | Foreign key to `courses` concept (no FK yet). |
| panopto_session_id | TEXT nullable | Derived from `panopto_url`; unique per `course_id`. |
| panopto_url | TEXT | Viewer URL kept for deep links. |
| stream_url | TEXT | Direct podcast URL used by the downloader. |
| title | TEXT nullable | Optional metadata. |
| audio_storage_key | TEXT nullable | Temp audio path `audio/{id}.m4a`. |
| transcript_storage_key | TEXT nullable | Points to the stored Whisper payload (`transcripts/{id}.json`). |
| duration_seconds | INT nullable | Extracted from audio via ffprobe. |
| status | lecture_status | Defaults to `pending`; reflects download job state. |
| error_message | TEXT nullable | Short failure reason (truncated in service). |
| created_at | TIMESTAMPTZ | Default `now()`. |
| updated_at | TIMESTAMPTZ | Default `now()`, auto-updated via trigger. |

Constraints & indexes:
- `UNIQUE(course_id, panopto_session_id)` prevents duplicate submissions.
- `idx_lectures_course_id` accelerates course filtering.
- Trigger `trg_lectures_updated_at` runs `set_updated_at()` before updates.

### documents
| Column | Type | Notes |
| --- | --- | --- |
| id | UUID PK | `gen_random_uuid()`. |
| owner_id | UUID FK | References `users(id)`; the document belongs to exactly one user. |
| course_id | UUID | Course grouping. |
| filename | TEXT | Original client filename. |
| storage_key | TEXT | Logical key `documents/{id}.pdf`. |
| checksum | TEXT | SHA256 for deduplication per course. |
| mime_type | TEXT | Derived from upload headers. |
| size_bytes | BIGINT | Captured during storage write. |
| page_count | INT nullable | Placeholder for future parsing. |
| description | TEXT nullable | Placeholder for AI summary. |
| status | document_status | Defaults to `uploaded`. |
| created_at / updated_at | TIMESTAMPTZ | Managed via defaults + trigger. |

Constraints & indexes:
- `UNIQUE(owner_id, course_id, checksum)` ensures dedup per user per course.
- `idx_documents_course_id`, `idx_documents_checksum` for lookups.
- Trigger `trg_documents_updated_at` updates `updated_at`.

### user_lectures
Associative table linking users to lectures.

| Column | Type | Notes |
| --- | --- | --- |
| user_id | UUID PK/FK | References `users(id)` ON DELETE CASCADE. |
| lecture_id | UUID PK/FK | References `lectures(id)` ON DELETE CASCADE. |
| created_at | TIMESTAMPTZ | Default `now()`. |

Indexes:
- `idx_user_lectures_lecture_id` (lecture → users lookup).

### courses
Holds canonical course metadata. Official courses are synced from CDCS catalog.

| Column | Type | Notes |
| --- | --- | --- |
| id | UUID PK | `gen_random_uuid()` default. |
| code | TEXT UNIQUE | e.g., `CSC 173`. Section suffixes stripped during sync. |
| title | TEXT | Full course title from CDCS. |
| instructor | TEXT nullable | Instructor name (not synced from CDCS). |
| is_official | BOOLEAN | `true` for CDCS-synced courses, `false` for user-created. |
| created_at / updated_at | TIMESTAMPTZ | Timestamps with auto-update trigger. |

Indexes:
- `uq_courses_code` keeps codes unique.

**Sync behavior**: The `CourseSyncService` populates this table from CDCS XML endpoint. Courses with `is_official=true` that are no longer in CDCS are deleted (with safety threshold check).

### users
Minimal table to satisfy foreign keys and cascading cleanup (id + timestamps). Rows are inserted lazily via `ensure_user_exists` whenever a user uploads data.

### Supporting Objects
- `set_updated_at()` trigger function ensures `updated_at` is refreshed on row updates for both `lectures` and `documents`.
- Migration script conditionally creates a minimal `users` table (id + created_at) if absent so foreign keys succeed in local development.

## Data Lifecycles
- **Lecture statuses**: transitions from `pending` → `downloading` → (`completed` | `failed`). `error_message` captures transient failures. Temporary storage keys are cleaned when `fail` occurs or when lecture is orphaned. Successful runs store audio in `audio/{lecture_id}.m4a` and Whisper transcription payloads (JSON + optional `.vtt`) in `transcripts/{lecture_id}.json` (registered in `transcript_storage_key`).
- **Document deduplication**: checksum uniqueness is scoped to the owner so each user controls their own uploads. Removing a document deletes the storage file immediately.

## Related Docs
- [Project Architecture](project_architecture.md)
- [Repository Index](../README.md)
