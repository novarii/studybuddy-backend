We are building studybuddy, an AI powered learning platform.
You are a senior backend engineer working in the repository studybuddy-fastapi. Your task is to implement

1. Panopto lecture audio downloads (metadata + jobs only, no transcription).
2. PDF document uploads and storage (with an abstraction that can later switch from local disk to DigitalOcean Spaces).

Follow these constraints exactly:

- Use FastAPI for the HTTP API surface.
- Use PostgreSQL as the single source of truth for metadata.
- Keep route handlers in app/main.py thin; put business logic in separate modules under app/ (e.g. lectures_service.py, documents_service.py, storage.py, downloader.py).
- Design file storage through a clear interface so that migrating from local disk to DigitalOcean Spaces later requires changing only configuration and the storage implementation, not API contracts or business logic.
- All new code must follow PEP 8, use type hints, and keep functions/modules small and single-purpose.

Storage requirements (local now, Spaces later):

- Define a storage interface, e.g. in app/storage.py, with methods like:
    - store_file(storage_key: str, file_obj: BinaryIO) -> StoredFileMeta
    - get_file_url(storage_key: str) -> str
    - delete_file(storage_key: str) -> None
- Implement this interface first with local disk (e.g. under storage/documents/ and storage/audio_tmp/).
- Do NOT expose absolute filesystem paths in any API responses. Clients should only see:
    - IDs (e.g. document_id, lecture_id) and/or
    - logical URLs based on storage_key via your backend (e.g. /api/documents/{id}/file) or pre-signed URLs
      later.
- In the database, store only:
    - Object ID (e.g. document.id, lecture.id),
    - Original filename,
    - Size and mime type,
    - A logical storage_key string (e.g. documents/{document_id}.pdf or audio/{lecture_id}.m4a),
      not full absolute paths.
- Design storage_key formats so they can be reused directly as object keys in DigitalOcean Spaces (S3-compatible) later.

Scope for this phase:

- Audio/video files for Panopto downloads are temporary on disk and will be deleted after transcription. You do NOT need to persist audio permanently or wire it to Spaces in this phase, but:
    - All access to files must still go through the storage abstraction.
    - The code must be written so that later we can decide to persist audio and back it with Spaces without changing public APIs.

Concrete deliverables:

1. Database schema and models (metadata only)

Implement migrations and ORM models for:

- lectures
    - Fields:
        - id UUID PK
        - course_id UUID (FK optional for now but modeled)
        - panopto_session_id TEXT NULL
        - panopto_url TEXT NOT NULL
        - title TEXT NULL
        - audio_storage_key TEXT NULL (temporary path like 'audio/{id}.m4a', deleted after transcription)
        - transcript_storage_key TEXT NULL (durable path like 'transcripts/{id}.json')
        - duration_seconds INT NULL
        - status lecture_status NOT NULL DEFAULT 'pending' (ENUM: 'pending', 'downloading', 'completed', 'failed')
        - error_message TEXT NULL
        - created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        - updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        - UNIQUE (course_id, panopto_session_id)
    - Indexes:
        - CREATE INDEX idx_lectures_course_id ON lectures(course_id)

- documents
    - Fields:
        - id UUID PK
        - course_id UUID (FK optional for now but modeled)
        - filename TEXT NOT NULL (original filename)
        - storage_key TEXT NOT NULL (logical key like 'documents/{id}.pdf')
        - checksum TEXT NOT NULL (SHA256 of file bytes)
        - mime_type TEXT NOT NULL
        - size_bytes BIGINT NOT NULL
        - page_count INT NULL (will be filled in later phases)
        - description TEXT NULL (AI-generated summary, filled in later phases)
        - status document_status NOT NULL DEFAULT 'uploaded' (ENUM: 'uploaded', 'failed')
        - created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        - updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        - UNIQUE (course_id, checksum)
    - Indexes:
        - CREATE INDEX idx_documents_course_id ON documents(course_id)
        - CREATE INDEX idx_documents_checksum ON documents(checksum)

- user_lectures (many-to-many)
    - Fields:
        - user_id UUID NOT NULL (FK to users, ON DELETE CASCADE)
        - lecture_id UUID NOT NULL (FK to lectures, ON DELETE CASCADE)
        - created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        - PRIMARY KEY (user_id, lecture_id)
    - Indexes:
        - CREATE INDEX idx_user_lectures_lecture_id ON user_lectures(lecture_id)

- user_documents (many-to-many)
    - Fields:
        - user_id UUID NOT NULL (FK to users, ON DELETE CASCADE)
        - document_id UUID NOT NULL (FK to documents, ON DELETE CASCADE)
        - created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        - PRIMARY KEY (user_id, document_id)
    - Indexes:
        - CREATE INDEX idx_user_documents_document_id ON user_documents(document_id)

- Add triggers to auto-update updated_at on lectures and documents tables.

Do NOT create transcript, chunk, or embedding tables in this phase. No pgvector columns yet.

Note: We removed the separate lecture_download_jobs table. Job status is tracked directly in the lectures table using the status field.

2. Panopto lecture audio download pipeline (metadata + jobs only)

Implement the ingestion surface and orchestration, but stop before transcription:

- Add POST /api/lectures/download in app/main.py with request body:

  {
    "course_id": "UUID",
    "user_id": "UUID",
    "panopto_url": "string",
    "title": "string (optional)"
  }
    - Validate course_id and user_id are valid UUIDs and panopto_url is non-empty.
    - Response:

  {
    "lecture_id": "UUID",
    "status": "pending"
  }

- Uniqueness handling:
    - Extract panopto_session_id from the URL.
    - Check if a lecture with the same (course_id, panopto_session_id) already exists.
    - If it exists:
        - Link the user to the existing lecture via user_lectures table if not already linked.
        - Return the existing lecture_id with status 200.
    - If it doesn't exist:
        - Create a new lecture row with status = 'pending'.
        - Link the user to the lecture via user_lectures table.
        - Return the new lecture_id with status 201.

- Implement app/lectures_service.py responsible for:
    - Creating a lectures row with status = 'pending'.
    - Creating a user_lectures link between the user and lecture.
    - Enqueuing a background task (FastAPI BackgroundTasks, or a clearly-defined worker interface) that:
        - Updates lectures.status to 'downloading', sets updated_at.
        - Uses a PanoptoDownloader abstraction (in app/downloader.py) to download the video using a temporary
          storage_key (e.g. audio/{lecture_id}_source.mp4) via the storage interface.
        - Uses an AudioExtractor abstraction to extract audio and write it under audio_storage_key (e.g.
          audio/{lecture_id}.m4a) via the storage interface.
        - Extracts duration_seconds from the audio file and updates the lecture record.
        - On success:
            - Delete the original video file via storage.delete_file().
            - Keep the audio file temporarily (audio_storage_key) for transcription in the next phase.
            - Set lectures.status = 'completed', updated_at.
        - On failure:
            - Set lectures.status = 'failed', populate error_message, set updated_at.
            - Clean up any temporary files via storage.delete_file().

- PanoptoDownloader and AudioExtractor must be separate, testable components with clean interfaces so they can
  be mocked in tests and later re-wired to Spaces-backed storage.

3. PDF document upload pipeline (with storage abstraction)

Implement basic upload and metadata persistence using the storage interface:

- Add POST /api/documents/upload in app/main.py that:
    - Accepts multipart/form-data with:
        - file: single PDF file.
        - course_id: required UUID field.
        - user_id: required UUID field.
    - Rejects non-PDF uploads (based on mime type and extension at minimum).

- Implement app/documents_service.py that:
    - Generates a new document_id (UUID).
    - Computes checksum (SHA256) of the file bytes.
    - Checks for duplicate: if a documents row with the same (course_id, checksum) exists:
        - Link the user to the existing document via user_documents table if not already linked.
        - Return the existing document_id with status 200.
    - If not a duplicate:
        - Constructs a storage_key like documents/{document_id}.pdf.
        - Uses the storage interface to write the file to that storage_key (local disk implementation for now
          under storage/documents/).
        - Computes:
            - size_bytes from the saved file,
            - mime_type,
            - preserves filename as the original client filename.
        - Inserts a documents row with:
            - id = document_id
            - course_id
            - filename (original)
            - storage_key (logical key, not absolute path)
            - checksum
            - mime_type
            - size_bytes
            - page_count = NULL
            - description = NULL
            - status = 'uploaded'
        - Creates a user_documents link between the user and document.
        - Returns a response:

          {
            "document_id": "UUID",
            "course_id": "UUID",
            "status": "uploaded"
          }

4. Status and inspection endpoints

Implement read-only endpoints:

- GET /api/lectures/{id}:
    - Returns: id, course_id, panopto_session_id, panopto_url, title, duration_seconds, status, error_message, timestamps.
    - Only accessible to users linked to this lecture via user_lectures.

- GET /api/lectures/{id}/status:
    - Returns a compact payload:

      {
        "lecture_id": "UUID",
        "status": "pending|downloading|completed|failed",
        "error_message": "string or null",
        "duration_seconds": "int or null"
      }
    - Only accessible to users linked to this lecture via user_lectures.

- GET /api/documents/{id}:
    - Returns: id, course_id, filename, mime_type, size_bytes, page_count, status, timestamps.
    - Do not expose storage_key, checksum, or absolute filesystem paths.
    - Only accessible to users linked to this document via user_documents.

- GET /api/documents/{id}/file:
    - Streams the actual PDF file to the client.
    - Uses storage.get_file_url() or streams directly via storage interface.
    - Only accessible to users linked to this document via user_documents.

5. Cleanup and garbage collection

Implement cleanup logic for orphaned resources:

- When a user is removed from user_lectures or user_documents:
    - Check if any other users are linked to that lecture/document.
    - If no users remain (orphaned resource):
        - Delete the lecture/document row from the database.
        - Delete associated files via storage.delete_file() for all storage_keys (audio_storage_key, transcript_storage_key, storage_key).

- This can be implemented as:
    - Database triggers that fire after DELETE on user_lectures/user_documents, OR
    - Python service methods that handle the cleanup logic with proper error handling.

6. Implementation structure

- Organize modules under app/:
    - app/db.py – DB session management.
    - app/models.py (or dedicated modules) – SQLAlchemy models for lectures, documents, user_lectures, user_documents.
    - app/storage.py – storage interface + local-disk implementation.
    - app/downloader.py – PanoptoDownloader, AudioExtractor.
    - app/lectures_service.py – lecture ingestion logic.
    - app/documents_service.py – PDF upload logic.