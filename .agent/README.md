# Documentation Index

This folder hosts all knowledge required to work on StudyBuddy Backend. Start here to find the right reference.

## System Docs
- [System/project_architecture.md](System/project_architecture.md) — Overall project goal, architecture, tech stack, API surface, chat streaming, and core flows.
- [System/database_schema.md](System/database_schema.md) — Detailed PostgreSQL schema, enums, constraints, and lifecycle notes.
- [System/slide_chunk_pipeline.md](System/slide_chunk_pipeline.md) — Implementation details for the slide extraction, AI chunking, and PgVector knowledge ingestion pipeline.
- [System/lecture_chunk_pipeline.md](System/lecture_chunk_pipeline.md) — Transcript-driven chunking pipeline for lectures plus ingestion/cleanup responsibilities.
- [System/agentos_testing.md](System/agentos_testing.md) — How to launch the AgentOS control plane locally and exercise the StudyBuddy chat agent without the frontend.
- [System/mcp_server.md](System/mcp_server.md) — Instructions for the MCP server that exposes StudyBuddy knowledge searches as MCP tools.

## Tasks
- `.agent/Tasks/` — Product requirements & implementation plans per feature (see file names for scope).
  - [Tasks/frontend_chat_integration.md](Tasks/frontend_chat_integration.md) — **Frontend handoff**: Next.js + Vercel AI SDK v5 integration for the streaming chat endpoint.
  - [Tasks/ai_sdk_nextjs_bridge_plan.md](Tasks/ai_sdk_nextjs_bridge_plan.md) — High-level plan for AI SDK UI → Next.js → Agno bridge architecture.
  - [Tasks/pdf_vector_pipeline_plan.md](Tasks/pdf_vector_pipeline_plan.md) — Plan for hashing slides, AI descriptions, custom chunking, and embedding ingestion.
  - [Tasks/studybuddy_initial_phase.md](Tasks/studybuddy_initial_phase.md) — Initial project phase planning.
  - [Tasks/audio_only_downloads_with_auth.md](Tasks/audio_only_downloads_with_auth.md) — Audio-only download requirements with authentication.
  - [Tasks/codebase_cleanup_plan.md](Tasks/codebase_cleanup_plan.md) — Codebase cleanup and refactoring plan.
  - [Tasks/critical_security_fixes_handoff.md](Tasks/critical_security_fixes_handoff.md) — Security fixes handoff document.

## SOPs
- `.agent/SOP/` — Step-by-step guides for common workflows (add new entries as needed).

## Key Implementation Files

### Chat Streaming (Vercel AI SDK v5)
- `app/adapters/vercel_stream.py` — `AgnoVercelAdapter` converts Agno events to SSE format, includes `RAGSource` with `chunk_number` for citation linking
- `app/agents/chat_agent.py` — Chat agent with custom RAG retriever, plus test agent for AgentOS playground
- `app/agents/context_formatter.py` — Separates lean model context (`[1] (Slide 5) ...`) from rich client metadata
- `app/main.py` — `POST /api/agent/chat` streaming endpoint

## Utilities
- `scripts/run_migrations.sh` — Apply all SQL migrations sequentially (run after `docker compose up -d db`).
- `scripts/test_transcription.py` — Manually upload a local `.m4a` file to the configured Whisper server to validate connectivity/polling.

Keep this index updated whenever new documentation is added or reorganized.
