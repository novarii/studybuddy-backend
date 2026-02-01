**Status:** Accepted

# Slide Chunk & Knowledge Pipeline

## Purpose
PDF uploads now trigger an AI-assisted pipeline that renders slide images, deduplicates near-identical pages, generates textual descriptions, and pushes those chunks into Agno’s PgVector-backed knowledge base. This document explains the moving pieces so new engineers can reason about ingestion cost, background work, and cleanup responsibilities.

## Components
- **`SlideExtractionService` (`app/services/pdf_slides_service.py`)** — Uses PyMuPDF to render each page under a configurable zoom, computes perceptual hashes via `imagehash`, and drops duplicate slides in-memory.
- **`SlideDescriptionAgent` (`app/agents/pdf_description_agent.py`)** — Wraps Agno + Gemini with a strict `SlideContent` schema; returns `SlideContentWithNumber` instances so slide numbers stay aligned even after deduplication.
- **`SlideChunkingService` (`app/services/pdf_slide_chunks_service.py`)** — Orchestrates extraction + description and returns `SlideChunkingResult` with normalized chunk strings (`Text: … | Images: … | …`).
- **`DocumentChunkPipeline` (`app/services/document_chunk_pipeline.py`)** — Background pipeline invoked after `/api/documents/upload` returns. It:
  1. Calls the chunking service using the PDF stored via `StorageBackend`.
  2. Persists a JSON artifact under `storage/document_chunks/{document_id}.json` for auditing / replays.
  3. Inserts each chunk into the slide knowledge base using Agno’s `Knowledge.add_content`.
  4. Raises `DocumentChunkPipelineError` if slide extraction or Gemini descriptions fail so the document can be deleted before the user interacts with it.
- **`knowledge_builder` (`app/agents/knowledge_builder.py`)** — Creates cached `Knowledge` instances backed by `PgVector`, using a Voyage AI embedder. The schema defaults to `public` but can be overridden. The builder ensures the schema exists, configures the embedder, and lets Agno auto-provision the underlying tables.

## Document Upload Flow (extended)
1. `/api/documents/upload` stores the PDF and, when a new record is created, schedules `_process_document_pipeline` on FastAPI background tasks.
2. The pipeline renders/deduplicates slides, writes the JSON artifact, and calls `knowledge.add_content(text_content=chunk.chunk_text, metadata={...})` for each unique slide. Metadata includes document, course, owner IDs, slide number, and slide type.
3. If slide extraction, Gemini descriptions, or artifact writing fail, `_process_document_pipeline` catches `DocumentChunkPipelineError`, deletes the document + artifacts, and logs the error so the client never sees a half-processed upload.

## Data Storage
- **Chunk artifacts**: `storage/document_chunks/{document_id}.json` contains a list of slide hashes, dimensions, structured descriptions, and chunk strings.
- **Vectors**: Agno auto-creates `public.slide_chunks_knowledge` (or the configured schema) with `content`, `embedding`, and metadata JSONB columns. Duplicate detection uses `content_hash`. Rows are filtered by metadata for per-user/course isolation.

## Deletion & Cleanup
- `DELETE /api/documents/{document_id}` now calls `DocumentChunkPipeline.cleanup_document`, which:
  - Deletes the chunk JSON artifact (best-effort).
  - Calls `knowledge.remove_vectors_by_metadata({"document_id": "<uuid>"})` so embeddings are purged when the document disappears.
- Be mindful when adding new storage prefixes or knowledge tables; cleanup must stay in sync.

## Configuration Requirements
Add the following env vars when deploying the slide pipeline:
- `VOYAGE_API_KEY` — Required; if absent the knowledge builder returns `None` and ingestion is skipped.
- `VOYAGE_MODEL_ID` (default `voyage-3-lite`) and `VOYAGE_EMBED_DIMENSIONS` (default `512`) — Must match the Voyage model you provision.
- `KNOWLEDGE_VECTOR_SCHEMA` — Defaults to `public`. Swapping schemas requires ensuring the DB user can `CREATE SCHEMA`.
- `SLIDE_KNOWLEDGE_TABLE` / `LECTURE_KNOWLEDGE_TABLE` — Table names used when instantiating `PgVector`.

PgVector is provided by running the `pgvector/pgvector:pg16` Docker image (see `docker-compose.yaml`). The first insert automatically runs `CREATE EXTENSION vector` and builds the knowledge table (schema migration is not tracked in `migrations/` because Agno manages it).

## Operational Notes
- **Voyage quotas**: Embedding failures surface as warnings in the app logs (`expected 512 dimensions, not 0`). When you see these, fix API billing before re-running the upload; the JSON artifact is still written so you can reprocess later.
- **Gemini fallbacks**: `_coerce_response` normalizes empty fields to `"None"`; normalization collapses whitespace so we never store raw multi-line strings.
- **Reprocessing**: If you need to rebuild knowledge after a config change, delete the JSON artifact and vector rows, then re-run `process_document` manually (e.g., via a management command or shell script).

## Related Docs
- [Lecture Chunk & Knowledge Pipeline](lecture_chunk_pipeline.md)
- [Project Architecture](project_architecture.md)
- [Database Schema](database_schema.md)
- [Task: PDF Vector Pipeline Plan](../Tasks/pdf_vector_pipeline_plan.md)
