# Backend Migration: Python → Next.js/TypeScript

**Status:** Planned

## Executive Summary

Migrate StudyBuddy backend from Python/FastAPI to Next.js API routes with Vercel AI SDK. Primary motivations:

1. **Remove Agno complexity** - Current agent framework requires workarounds (message ID mismatches, custom adapters, opaque session storage)
2. **Unified stack** - Frontend already Next.js/TypeScript; unify with backend
3. **Native AI SDK** - Direct streaming, no adapter layer
4. **BYOK model** - Users authenticate with OpenRouter OAuth, use their own API keys

## Current Architecture (Python)

```
studybuddy-backend/
├── app/
│   ├── main.py                 # FastAPI routes (~900 lines)
│   ├── adapters/
│   │   └── vercel_stream.py    # Agno → Vercel SSE adapter (DELETE)
│   ├── agents/
│   │   └── chat_agent.py       # Agno agent + RAG tools
│   ├── services/
│   │   ├── documents_service.py
│   │   ├── lectures_service.py
│   │   ├── document_chunk_pipeline.py
│   │   ├── lecture_chunk_pipeline.py
│   │   ├── transcription_service.py
│   │   └── message_sources_service.py
│   ├── database/
│   │   ├── db.py               # SQLAlchemy session
│   │   └── models.py           # ORM models
│   └── storage/
│       └── local_backend.py    # File storage
└── migrations/                  # SQL migrations
```

### Current Dependencies

| Category | Python | TypeScript Equivalent |
|----------|--------|----------------------|
| Web framework | FastAPI | Next.js API routes |
| ORM | SQLAlchemy 2.0 | Prisma or Drizzle |
| Auth | Clerk Backend SDK | Clerk Next.js SDK |
| LLM | Agno + OpenRouter | Vercel AI SDK + OpenRouter |
| Embeddings | Voyage AI | OpenRouter embeddings |
| Vector DB | pgvector | pgvector (same) |
| PDF parsing | PyMuPDF | pdf-lib / pdfjs-dist |
| Audio | FFmpeg (subprocess) | FFmpeg (child_process) |
| Background jobs | FastAPI BackgroundTasks | Inngest / BullMQ / Trigger.dev |

## Target Architecture (Next.js)

```
studybuddy-frontend/        # Becomes full-stack
├── app/
│   ├── api/
│   │   ├── chat/route.ts           # AI SDK streamText
│   │   ├── sessions/[id]/route.ts  # Session CRUD
│   │   ├── documents/route.ts      # Upload, list, delete
│   │   ├── lectures/route.ts       # Lecture management
│   │   └── auth/
│   │       └── openrouter/route.ts # OAuth PKCE callback
│   └── (existing pages)
├── lib/
│   ├── db/
│   │   ├── schema.ts       # Drizzle/Prisma schema
│   │   └── client.ts       # DB connection
│   ├── ai/
│   │   ├── chat.ts         # AI SDK chat logic
│   │   ├── embeddings.ts   # OpenRouter embeddings
│   │   └── tools.ts        # RAG search tool
│   ├── services/
│   │   ├── documents.ts
│   │   ├── lectures.ts
│   │   └── sessions.ts
│   └── storage/
│       └── local.ts        # Or S3/R2
└── (existing components, hooks, etc.)
```

## Database Schema

**No changes to PostgreSQL schema.** Same tables:
- `users`, `courses`, `user_courses`
- `documents`, `lectures`, `user_lectures`
- `ai.slide_chunks_knowledge`, `ai.lecture_chunks_knowledge` (pgvector)
- `ai.message_sources` (RAG citation persistence)

**New/Modified:**
- Replace `ai.agno_sessions` with simpler `chat_sessions` + `chat_messages` tables
- User table gets `openrouter_key_encrypted` for BYOK

## Migration Phases

### Phase 1: Chat + RAG (Core)
**Goal:** Replace Agno with AI SDK, prove the pattern

- [ ] Set up Drizzle/Prisma with existing PostgreSQL
- [ ] Create `chat_sessions` and `chat_messages` tables
- [ ] Implement `/api/chat` with AI SDK `streamText()`
- [ ] Implement RAG search tool (reuse pgvector queries)
- [ ] Session CRUD endpoints
- [ ] Message sources persistence (port from Python)

**Deliverable:** Chat works end-to-end with AI SDK

### Phase 2: OpenRouter BYOK
**Goal:** Users bring their own OpenRouter keys

- [ ] OAuth PKCE flow (callback route)
- [ ] Store encrypted API key per user
- [ ] Inject user's key into all OpenRouter calls
- [ ] "Connect OpenRouter" UI flow

**Deliverable:** Zero LLM cost for operator

### Phase 3: Document Pipeline
**Goal:** PDF upload and processing in Node

- [ ] File upload endpoint (multipart)
- [ ] PDF parsing (pdf-lib or pdfjs-dist)
- [ ] Slide extraction
- [ ] AI descriptions via OpenRouter
- [ ] Chunking + embedding via OpenRouter
- [ ] pgvector ingestion

**Deliverable:** PDF upload works end-to-end

### Phase 4: Lecture Pipeline
**Goal:** Audio processing (may keep Python service)

- [ ] Evaluate: migrate vs. keep Python microservice
- [ ] Audio extraction (FFmpeg - same either way)
- [ ] Whisper transcription (serverless GPU? Replicate? Modal?)
- [ ] Transcript chunking + embedding

**Deliverable:** Lecture ingestion works

### Phase 5: Cleanup
- [ ] Remove Python backend
- [ ] Remove Agno dependency
- [ ] Update deployment (single Next.js app)
- [ ] Update documentation

## Key Implementation Details

### AI SDK Chat Endpoint

```typescript
// app/api/chat/route.ts
import { streamText } from 'ai';
import { createOpenRouter } from '@openrouter/ai-sdk-provider';

export async function POST(req: Request) {
  const { messages, sessionId } = await req.json();
  const user = await getUser(req);

  const openrouter = createOpenRouter({
    apiKey: decrypt(user.openrouterKeyEncrypted),
  });

  const result = streamText({
    model: openrouter('anthropic/claude-sonnet-4'),
    messages,
    tools: {
      search_course_materials: {
        description: 'Search lecture transcripts and slides',
        parameters: z.object({
          query: z.string(),
          courseId: z.string(),
        }),
        execute: async ({ query, courseId }) => {
          return await searchKnowledge(query, courseId, user.id);
        },
      },
    },
  });

  return result.toDataStreamResponse();
}
```

### Embeddings via OpenRouter

```typescript
// lib/ai/embeddings.ts
export async function embed(text: string, apiKey: string): Promise<number[]> {
  const response = await fetch('https://openrouter.ai/api/v1/embeddings', {
    method: 'POST',
    headers: {
      'Authorization': `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: 'openai/text-embedding-3-small',
      input: text,
    }),
  });
  const { data } = await response.json();
  return data[0].embedding;
}
```

### OpenRouter OAuth PKCE

```typescript
// app/api/auth/openrouter/route.ts
export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const code = searchParams.get('code');

  // Exchange code for API key
  const response = await fetch('https://openrouter.ai/api/v1/auth/keys', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, code_verifier: getStoredVerifier() }),
  });

  const { key } = await response.json();

  // Store encrypted key for user
  await db.update(users)
    .set({ openrouterKeyEncrypted: encrypt(key) })
    .where(eq(users.id, userId));

  return redirect('/');
}
```

## Risks & Mitigations

| Risk | Mitigation |
|------|------------|
| PDF parsing quality differs | Test with existing documents, compare output |
| Background job reliability | Use established solution (Inngest, Trigger.dev) |
| Whisper integration | Keep Python microservice or use Replicate/Modal |
| Data migration | No migration needed - same PostgreSQL |
| Downtime during switch | Run both backends, feature flag, cut over |

## Success Criteria

1. All existing functionality works
2. No Agno dependency
3. Users can connect OpenRouter (BYOK)
4. Single deployable unit (Next.js)
5. Same or better performance

## Related Specs

- [Architecture](../architecture.md)
- [RAG System](../rag-system.md)
- [Database Schema](../database-schema.md)

## Next Steps

1. Start with Phase 1 spec: `01-chat-rag-migration.md`
2. Set up Drizzle/Prisma in frontend repo
3. Implement chat endpoint with AI SDK
4. Prove pattern works before migrating other services
