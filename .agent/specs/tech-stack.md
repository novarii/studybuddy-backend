# Technology Stack

**Status:** Accepted

## Core Technologies

### API Framework
- **FastAPI 0.115+** with async/await
- Pydantic schemas in `app/schemas/`
- Dependencies/helpers in `app/api/`

### Database
- **PostgreSQL** with pgvector extension for vector embeddings
- **SQLAlchemy 2.0+** ORM (`app/database/`)
- SQL migrations under `migrations/versions/`

### AI & ML
- **Agno 2.3.x** - Agent framework with multi-LLM support
- **Voyage AI** - Vector embeddings and semantic search (512-dimensional)
- **Google Gemini** - Primary LLM for chat and slide descriptions
- **OpenRouter** - Alternative LLM provider (optional)
- **pgvector** - Vector similarity search in PostgreSQL

### Media Processing
- **FFmpeg/FFprobe** - Audio extraction from video files
- **PyMuPDF** - PDF parsing and slide extraction
- **Whisper** - Remote FastAPI server for transcription

### Authentication & Integration
- **Clerk Backend API** - User authentication with JWT tokens
- **FastMCP 2.13+** - Model Context Protocol server

### Storage
- **LocalStorageBackend** - Current implementation (filesystem)
- Designed for easy swapping to S3/cloud storage

## Development Tools

### Package Management
- **uv** - Fast Python package installer and resolver
- Virtual environment management

### Container Runtime
- **Docker** - PostgreSQL with pgvector via `docker-compose.yaml`

## Environment Configuration

### Required Environment Variables
- `DATABASE_URL` - PostgreSQL connection string
- `STORAGE_ROOT` - Root directory for file storage
- `CLERK_SECRET_KEY` - Clerk authentication
- `CLERK_AUTHORIZED_PARTIES` - Allowed JWT audiences
- `VOYAGE_API_KEY` - Voyage AI embeddings
- `CORS_ALLOW_ORIGINS` - Frontend origins

### Optional Variables
- `WHISPER_SERVER_IP`, `WHISPER_SERVER_PORT` - Transcription service
- `OPENROUTER_API_KEY` - Alternative LLM provider
- `GOOGLE_API_KEY` - Gemini API key
- `ADMIN_USER_IDS` - Comma-separated admin UUIDs

## Related Specs
- [Architecture](./architecture.md)
- [Database Schema](./database-schema.md)
- [Authentication](./authentication.md)
