# System Architecture

**Status:** Accepted

## Overview

StudyBuddy Backend is a FastAPI service that orchestrates Panopto lecture audio downloads, transcription, and PDF document uploads for AI-powered study workflows. PostgreSQL stores all metadata, while binary assets live behind a swappable storage abstraction (local disk currently). Route handlers in `app/main.py` stay thin by delegating to service modules.

## Layered Structure

```
Routes (app/main.py)
  → Services (app/services/*)
    → Storage/Database (app/storage/, app/database/)
```

### Architecture Patterns
- **Thin routes**: HTTP handlers in `app/main.py` delegate to service modules
- **Dependency injection**: FastAPI `Depends()` for DB sessions, services
- **Background tasks**: Use `FastAPI.BackgroundTasks` for long-running operations (transcription, chunking)
- **Storage abstraction**: Database stores logical keys (e.g., `documents/{document_id}.pdf`), not absolute paths

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
    course_sync_service.py    # CDCS catalog sync (scrapes official courses)
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

## Key Components

### Service Layer (`app/services/`)
- `LecturesService` - Orchestrates Panopto downloads, audio extraction, transcription
- `DocumentsService` - Manages PDF uploads with SHA256 deduplication per user per course
- `TranscriptionService` - Polls Whisper server for transcripts
- `LectureChunkPipeline` - Chunks transcript into ~180-second segments for knowledge base
- `DocumentChunkPipeline` - Extracts slides, generates AI descriptions, chunks for knowledge
- `CourseSyncService` - Scrapes CDCS catalog for official courses

### Agents Layer (`app/agents/`)
- `ChatAgent` - User-facing course Q&A with RAG (retrieval-augmented generation)
- `PDFDescriptionAgent` - Generates AI-powered slide descriptions
- `KnowledgeBuilder` - Manages Voyage AI embeddings and PgVector ingestion
- Retrieval System - Semantic search across lectures and documents with metadata filtering

### Storage Abstraction (`app/storage/`)
- `LocalStorageBackend` - Persists files with logical storage keys (not absolute paths)
- Files stored under `storage/documents/` and `storage/audio_tmp/`
- Swappable design allows S3/cloud storage backends

### Database Layer (`app/database/`)
- SQLAlchemy models: Users, Courses, Lectures, Documents, UserLectures
- Knowledge tables: `ai.slide_chunks_knowledge` and `ai.lecture_chunks_knowledge` (pgvector)
- Status enums: `LectureStatus` (pending/downloading/completed/failed), `DocumentStatus` (uploaded/failed)

### MCP Server (`app/mcp/`)
- Exposes course material retrieval as an LLM-accessible tool via FastMCP

## Related Specs
- [Tech Stack](./tech-stack.md)
- [Database Schema](./database-schema.md)
- [RAG System](./rag-system.md)
- [Storage Backend](./storage-backend.md)
