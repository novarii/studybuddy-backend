# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

StudyBuddy Backend is a FastAPI-based educational content platform that ingests Panopto lectures and PDF documents, processes them with AI (transcription, slide description), chunks content into semantic units, indexes them via Voyage AI embeddings into pgvector, and serves a Chat Agent (Agno-based) that answers student questions using retrieved course materials.

## Technology Stack

- **FastAPI 0.115+** with async/await
- **PostgreSQL** with pgvector extension for vector embeddings
- **SQLAlchemy 2.0+** ORM
- **Agno 2.3.x** agent framework with multi-LLM support
- **FastMCP 2.13+** for Model Context Protocol server
- **Voyage AI** for vector embeddings and semantic search
- **FFmpeg/FFprobe** for audio extraction from video
- **PyMuPDF** for PDF parsing and slide extraction
- **Clerk Backend API** for user authentication

## Documentation

This project follows **spec-driven development**:

### What is a Spec?
A **spec** is an atomic source of truth document that contains:
- Requirements and constraints
- Architecture decisions and rationale
- Code patterns and guidelines
- Implementation standards

**Key principles:**
- 1 topic of concern = 1 spec file
- Specs are referenced by implementation tasks
- Implementation plans should be self-contained (reference specs or include all needed info)

### Directory Structure
- **`specs/`** - Specification documents (architecture, standards, patterns)
  - `specs/README.md` - Lookup table of all specs with descriptions
- **`tasks/`** - Implementation plans that reference specs
- **`archives/`** - Historical audits and completed work

**See `specs/README.md` for the complete spec lookup table.**

## Development Commands

### Setup
```bash
source .venv/bin/activate
uv sync
```

### Database
```bash
docker compose up -d db                    # Start PostgreSQL with pgvector
./scripts/run_migrations.sh               # Apply all SQL migrations in order
```

### Running the Server
```bash
# Development with hot-reload
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Utility Scripts
```bash
python scripts/extract_pdf_slides.py      # Extract slides from PDF
python scripts/generate_slide_chunks.py   # Generate slide chunks
python scripts/test_transcription.py      # Test Whisper transcription
python scripts/copy_transcript_text.py    # Migrate transcript data
```

### Environment Variables
Required:
- `DATABASE_URL` (default: `postgresql://postgres:postgres@localhost:5432/studybuddy`)
- `STORAGE_ROOT` (default: `storage/`)
- `WHISPER_SERVER_IP` and `WHISPER_SERVER_PORT` for transcription pipeline
- `CLERK_SECRET_KEY` and `CLERK_AUTHORIZED_PARTIES` for auth
- `VOYAGE_API_KEY` for vector embeddings
- `CORS_ALLOW_ORIGINS` for frontend integration

Optional:
- `DOCUMENTS_STORAGE_PREFIX`, `AUDIO_TEMP_STORAGE_PREFIX`
- `WHISPER_REQUEST_TIMEOUT_SECONDS`, `WHISPER_POLL_INTERVAL_SECONDS`, `WHISPER_POLL_TIMEOUT_SECONDS`
- `OPENROUTER_API_KEY` for alternative LLM provider
- `ADMIN_USER_IDS` (comma-separated UUIDs)

Note: `ffmpeg` and `ffprobe` binaries must be on `PATH`.

## Architecture

### Layered Structure
```
Routes (app/main.py)
  → Services (app/services/*)
    → Storage/Database (app/storage/, app/database/)
```

### Key Components

**Service Layer** (`app/services/`):
- `LecturesService` - Orchestrates Panopto downloads, audio extraction, transcription
- `DocumentsService` - Manages PDF uploads with SHA256 deduplication per user per course
- `TranscriptionService` - Polls Whisper server for transcripts
- `LectureChunkPipeline` - Chunks transcript into ~180-second segments for knowledge base
- `DocumentChunkPipeline` - Extracts slides, generates AI descriptions, chunks for knowledge

**Agents Layer** (`app/agents/`):
- `ChatAgent` - User-facing course Q&A with RAG (retrieval-augmented generation)
- `PDFDescriptionAgent` - Generates AI-powered slide descriptions
- `KnowledgeBuilder` - Manages Voyage AI embeddings and PgVector ingestion
- Retrieval System - Semantic search across lectures and documents with metadata filtering

**Storage Abstraction** (`app/storage/`):
- `LocalStorageBackend` - Persists files with logical storage keys (not absolute paths)
- Files stored under `storage/documents/` and `storage/audio_tmp/`

**Database Layer** (`app/database/`):
- SQLAlchemy models: Users, Courses, Lectures, Documents, UserLectures
- Knowledge tables: `ai.slide_chunks_knowledge` and `ai.lecture_chunks_knowledge` (pgvector)
- Status enums: `LectureStatus` (pending/downloading/completed/failed), `DocumentStatus` (uploaded/failed)

**MCP Server** (`app/mcp/`):
- Exposes course material retrieval as an LLM-accessible tool via FastMCP

### Data Flow Examples

**Document Upload:**
1. User uploads PDF → FastAPI validates content type
2. DocumentsService computes SHA256 checksum for deduplication
3. File stored in `storage/documents/{document_id}.pdf`
4. Background task: DocumentChunkPipeline processes in queue
5. Extract slides via PyMuPDF → PDFDescriptionAgent describes each
6. Chunks stored + ingested into Voyage AI embeddings
7. Vector rows written to `ai.slide_chunks_knowledge` table

**Lecture Download:**
1. User requests Panopto lecture download
2. LecturesService validates URL → extracts session ID
3. PanoptoPackageDownloader fetches video
4. FFmpegAudioExtractor extracts audio → `storage/audio_tmp/{lecture_id}.m4a`
5. WhisperTranscriptionClient submits to Whisper server
6. LectureChunkPipeline groups transcript segments into ~180sec chunks
7. Chunks + metadata stored + ingested into Voyage AI
8. Vector rows written to `ai.lecture_chunks_knowledge` table

### RAG & Knowledge Retrieval

- **Dual retriever**: Searches slides first (per-user, per-course), then lectures (per-course)
- **Metadata filtering**: by owner_id, course_id, document_id, lecture_id
- **Voyage AI embeddings**: 512-dimensional vectors (configurable)
- **MCP integration**: Exposes retrieval as a tool for external agents

## Important Patterns & Conventions

### Architecture Patterns
- **Thin routes**: HTTP handlers in `app/main.py` delegate to service modules
- **Dependency injection**: FastAPI `Depends()` for DB sessions, services
- **Background tasks**: Use `FastAPI.BackgroundTasks` for long-running operations (transcription, chunking)
- **Storage abstraction**: Database stores logical keys (e.g., `documents/{document_id}.pdf`), not absolute paths

### Database Design
- **Deduplication**: Documents use `(owner_id, course_id, checksum)` unique constraint
- **Lecture-course coupling**: `(course_id, panopto_session_id)` unique constraint
- **Vector tables**: Separate tables for slide chunks and lecture chunks in `ai` schema

### API Design
- RESTful endpoints: `GET /api/courses`, `POST /api/lectures/download`, `DELETE /api/documents/{id}`
- Response codes: 201 Created for new resources, 200 OK for existing (deduplication cases)
- Streaming: `StreamingResponse` for PDF downloads
- Authentication: Clerk JWT tokens required via `require_user` dependency
- Development routes: `/api/dev/lectures` (admin-only when enabled)

### Code Style
- Follow **PEP 8**: 4-space indentation, snake_case functions, PascalCase for classes
- Use type hints throughout (Python 3.12+)
- Add docstrings for functions with side effects
- Configuration via environment variables + Settings dataclass

## Adding New Specs

When adding a new spec:
1. Identify the **topic of concern** (one topic per spec)
2. Create `specs/{topic-name}.md`
3. Include `**Status:** Accepted` at the top
4. Add to the lookup table in `specs/README.md`
5. Link from related specs if needed

When creating implementation plans:
- Create tasks in `tasks/{feature-name}.md`
- Reference relevant specs instead of duplicating information
- Include context if no spec exists for the topic

## Migration Management

The `./scripts/run_migrations.sh` script applies every SQL migration under `migrations/versions/` in order. Always run this after pulling new migrations to ensure schema matches the latest code.

## Git Workflow

Main branch: `main` (use this for PRs)

Recent feature branches demonstrate active development patterns:
- `feature/chat-agent` - Multi-LLM chat with RAG
- `feature/audio-to-text` - Whisper transcription pipeline
- `feature/lecture-chunking` - Transcript chunking
- `feature/pdf-description` - Slide descriptions via AI agents
