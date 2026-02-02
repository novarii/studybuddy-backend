# Specs Directory

This directory contains specification documents for StudyBuddy Backend, following spec-driven development principles.

## What is a Spec?

A **spec** is an atomic source of truth document. It can contain:
- Requirements and constraints
- Architecture decisions and rationale
- Code patterns and guidelines
- Implementation standards

**Key principles:**
- 1 topic of concern = 1 spec file
- Specs are referenced by implementation tasks
- Implementation plans should be self-contained (reference specs or include all needed info)

---

## Spec Lookup Table

*Last updated: 2026-02-02*

| Spec | Description | Key Topics |
|------|-------------|------------|
| [migration/00-overview.md](./migration/00-overview.md) | Backend migration plan | Python → Next.js/TypeScript, AI SDK, OpenRouter BYOK |
| [architecture.md](./architecture.md) | System architecture and design | Layered structure, service layer, agents, data flows |
| [tech-stack.md](./tech-stack.md) | Technology choices and rationale | FastAPI, SQLAlchemy, Agno, Voyage AI, pgvector |
| [database-schema.md](./database-schema.md) | Database design and relationships | Tables, constraints, indexes, status enums |
| [authentication.md](./authentication.md) | Clerk integration and auth flow | JWT verification, user resolution, admin overrides |
| [rag-system.md](./rag-system.md) | RAG architecture | Dual retriever, embeddings, metadata filtering |
| [lecture-ingestion.md](./lecture-ingestion.md) | Lecture processing pipeline | Two-path ingestion, Whisper, deduplication |
| [lecture-chunking.md](./lecture-chunking.md) | Transcript chunking strategy | ~180s segments, knowledge ingestion, cleanup |
| [document-processing.md](./document-processing.md) | PDF processing pipeline | Slide extraction, AI description, vector ingestion |
| [chat-agent.md](./chat-agent.md) | Chat agent design | Instructions, LLM selection, streaming, citations |
| [session-persistence.md](./session-persistence.md) | Chat session management | Agno database integration, session CRUD |
| [mcp-server.md](./mcp-server.md) | Model Context Protocol integration | FastMCP, tool exposure, retrieval |
| [storage-backend.md](./storage-backend.md) | Storage abstraction layer | Logical keys, local backend, swappable design |
| [security-requirements.md](./security-requirements.md) | Security standards | CORS, auth, file limits, path traversal, injection |
| [message-sources-persistence.md](./message-sources-persistence.md) | RAG sources persistence | message_sources table, save/load sources with messages |

---

## Adding New Specs

When adding a new spec:

1. Identify the **topic of concern** (one topic per spec)
2. Create `specs/{topic-name}.md`
3. Include `**Status:** Accepted` at the top
4. Add to the lookup table above
5. Link from related specs if needed
