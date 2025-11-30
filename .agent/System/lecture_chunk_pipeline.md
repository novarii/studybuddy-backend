# Lecture Chunk & Knowledge Pipeline

## Purpose
Recorded lectures now include a transcript-driven chunking pass so we can push ~180 second snippets into Agno's PgVector knowledge base. The new pipeline fans out per user, which ensures each learner only searches vectors for lectures they have explicit access to.

## Components
- **`LectureChunkPipeline` (`app/services/lecture_chunk_pipeline.py`)** — Normalizes Whisper segments, groups them into ~180s windows, writes the chunk JSON artifact, and ingests each chunk into the lecture knowledge base.
- **`LecturesService` (`app/services/lectures_service.py`)** — After transcription finishes, it calls `lecture_chunk_pipeline.process_transcript_segments(...)` with the lecture/course IDs plus every user linked via `user_lectures`. When a user leaves a lecture, it invokes `remove_user_chunks` so their vectors disappear; deleting the lecture runs `cleanup_lecture` to remove every artifact.
- **`knowledge_builder` (`app/agents/knowledge_builder.py`)** — Provides the cached `get_lecture_knowledge()` instance backed by Voyage + PgVector using the `LECTURE_KNOWLEDGE_TABLE` env var (defaults to `lecture_chunks_knowledge`).

## Chunking Flow
1. After FFmpeg audio extraction completes and Whisper returns a transcript, `LecturesService` stores the raw payload under `storage/transcripts/{lecture_id}.json` (and VTT when present).
2. `LectureChunkPipeline` receives the Whisper `segments` array. It drops malformed entries, sorts by start time, and collects consecutive segments until the elapsed audio time reaches roughly 180 seconds (~3 minutes). Each chunk stores:
   - `chunk_index`
   - `start` / `end` timestamps (seconds)
   - concatenated `text`
   - `segment_count`
3. The pipeline writes `storage/lecture_chunks/{lecture_id}.json` containing metadata (`lecture_id`, `course_id`, `chunk_duration_seconds`, and the chunk list) so we can reprocess or audit.
4. Every chunk is ingested into PgVector using `knowledge.add_content(text_content=chunk.text, metadata={...})` once per user linked to the lecture. Metadata includes `lecture_id`, `course_id`, `user_id`, `start_seconds`, `end_seconds`, and `chunk_index` so downstream filters and deletions remain cheap.

## Data Retention & Cleanup
- **Artifacts** live under `storage/lecture_chunks/` alongside transcript/VTT outputs. They are deleted when a lecture is removed or when no transcript segments are available.
- **Vectors** live inside the `LECTURE_KNOWLEDGE_TABLE` table (default `lecture_chunks_knowledge`). `remove_user_chunks` deletes rows scoped to `{"lecture_id": ..., "user_id": ...}` whenever a learner is detached from the lecture. Full lecture cleanup uses `cleanup_lecture` to purge both the JSON artifact and all vectors tied to `lecture_id`.

## Configuration
The lecture pipeline reuses the Voyage + PgVector settings already documented for slides:
- `VOYAGE_API_KEY`, `VOYAGE_MODEL_ID`, and `VOYAGE_EMBED_DIMENSIONS` must be configured or the lecture pipeline will skip ingestion (artifact is still written).
- `KNOWLEDGE_VECTOR_SCHEMA` selects the schema (default `ai`).
- `LECTURE_KNOWLEDGE_TABLE` controls the PgVector table name for lecture chunks.

## Related Docs
- [Slide Chunk & Knowledge Pipeline](slide_chunk_pipeline.md)
- [Project Architecture](project_architecture.md)
- [Database Schema](database_schema.md)
