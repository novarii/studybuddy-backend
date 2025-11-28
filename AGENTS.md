# Repository Guidelines

## Docs
- We keep all important docs in .agent folder and keep them updated. This is the structure:
.agent
- Tasks: PRD & implementation plan for each feature
- System: Document the current state of the system (project structure, tech stack, integration points, database schema, and core functionalities)
- SOP: Best practices of execute certain tasks (e.g. how to add a schema migration, how to add a new endpoint, etc.)
- README.md: an index of all the documentations we have so people know what & where to look for things

## Project Structure & Module Organization
- `app/main.py` hosts FastAPI routes; business logic lives in service modules like `app/lectures_service.py`, `app/documents_service.py`, `app/storage.py`, and `app/downloader.py`. SQLAlchemy models go in `app/models.py`. Add new features under `app/` instead of expanding route handlers directly.
- Storage uses a clear interface in `app/storage.py` with local disk implementation. Files live under `storage/documents/` and `storage/audio_tmp/`. Database stores only logical storage keys, not absolute paths.

## Build and Development Commands
- `python -m venv .venv && source .venv/bin/activate` — create a clean env before dependency installs.
- `uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload` — local dev server with auto-reload; use `uv run app.main` for parity with production.

## Coding Style & Naming Conventions
- Follow PEP 8 with 4-space indentation, snake_case functions, and PascalCase for Pydantic models and SQLAlchemy classes. Keep type hints and docstrings describing side effects.
- Route handlers should remain thin: place database queries, file operations, and external API calls inside service modules. Keep configuration (storage paths, database URLs) in environment variables or config files.
- Database operations should use SQLAlchemy ORM; prefer service methods over inline queries to keep routes readable.

## Important notice
- Run local git commands with higher permissions settings a recent macOS sandbox change blocked Codex from writing inside .git