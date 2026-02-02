# Message Sources Persistence

**Status:** Implemented

## Overview

Persist RAG sources in a separate `ai.message_sources` table so citations work when loading message history (page refresh, session switch).

## Problem

Currently, RAG sources are streamed via SSE during chat but NOT persisted. When users:
- Refresh the page
- Switch to another session and back
- Load chat history

...the citation references `[1]`, `[2]` become non-functional because sources are lost.

## Solution

Store sources in a new `ai.message_sources` table, keyed by `message_id`. Load sources when fetching message history.

---

## Database Schema

**Table:** `ai.message_sources`

```sql
CREATE TABLE IF NOT EXISTS ai.message_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    chunk_number INT NOT NULL,
    content_preview TEXT,
    document_id UUID,
    slide_number INT,
    lecture_id UUID,
    start_seconds FLOAT,
    end_seconds FLOAT,
    course_id UUID,
    owner_id UUID,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_message_source UNIQUE (message_id, source_id)
);

CREATE INDEX idx_message_sources_message_id ON ai.message_sources(message_id);
CREATE INDEX idx_message_sources_session_id ON ai.message_sources(session_id);
```

**Design decisions:**
- `message_id` is TEXT (Agno generates string UUIDs)
- `session_id` denormalized for efficient lookups
- `UNIQUE(message_id, source_id)` prevents duplicates
- No FK to Agno tables (they manage their own schema)

---

## Implementation

### 1. Migration

**File:** `migrations/versions/007_create_message_sources.sql`

### 2. New Service

**File:** `app/services/message_sources.py`

```python
def save_message_sources(
    db_session: Session,
    *,
    message_id: str,
    session_id: str,
    sources: List[RAGSource],
) -> None:
    """Persist RAG sources for an assistant message."""
    # INSERT ... ON CONFLICT DO NOTHING

def load_sources_for_messages(
    db_session: Session,
    message_ids: List[str],
) -> dict[str, List[RAGSource]]:
    """Load sources for multiple messages. Returns message_id -> sources mapping."""
```

### 3. Adapter Changes

**File:** `app/adapters/vercel_stream.py`

Add source collection to `AgnoVercelAdapter`:

```python
class AgnoVercelAdapter:
    def __init__(self, ...):
        self._collected_sources: List[RAGSource] = []
        self._agno_run_id: Optional[str] = None

    def _emit_all_sources(self, sources: List[RAGSource]) -> str:
        self._collected_sources.extend(sources)
        # ... existing emit logic ...

    @property
    def collected_sources(self) -> List[RAGSource]:
        return self._collected_sources

    @property
    def agno_run_id(self) -> Optional[str]:
        """Return the Agno run_id captured from RunCompletedEvent."""
        return self._agno_run_id
```

### 4. Schema Updates

**File:** `app/schemas/__init__.py`

```python
class RAGSourceResponse(BaseModel):
    source_id: str
    source_type: str
    content_preview: str
    chunk_number: int
    document_id: Optional[str] = None
    slide_number: Optional[int] = None
    lecture_id: Optional[str] = None
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    course_id: Optional[str] = None
    title: Optional[str] = None

class MessageResponse(BaseModel):
    id: str
    role: str
    content: str
    created_at: Optional[datetime] = None
    sources: Optional[List[RAGSourceResponse]] = None  # NEW
```

### 5. Chat Endpoint - Persist Sources

**File:** `app/main.py` (~line 621)

After streaming completes, persist sources using the native Agno message ID.

**Key insight:** The adapter generates its own `message_id` for SSE streaming, but Agno stores messages with different IDs. We must use Agno's native ID for sources to match when loading history.

**Agno data model:**
- `RunCompletedEvent.run_id` identifies the run (agent execution)
- `session.get_run(run_id)` returns `RunOutput` containing `messages`
- Each `message` in `run.messages` has its own `id`

**Implementation:**

```python
async def generate_stream():
    adapter = AgnoVercelAdapter()
    agent = None
    try:
        agent = create_chat_agent(db=agno_db, tools=[search_tool])
        stream = agent.run(...)
        for chunk in adapter.transform_stream_sync(stream):
            yield chunk
    finally:
        # adapter.agno_run_id captured from RunCompletedEvent
        if adapter.collected_sources and payload.session_id and adapter.agno_run_id and agent:
            # Traverse: session → run → message to get native message ID
            session = agent.get_session(session_id=payload.session_id)
            if session:
                run = session.get_run(adapter.agno_run_id)
                if run and run.messages:
                    for msg in reversed(run.messages):
                        if msg.role == "assistant" and msg.id:
                            save_message_sources(
                                db_session,
                                message_id=msg.id,  # Native Agno message ID
                                session_id=payload.session_id,
                                sources=adapter.collected_sources,
                            )
                            break
```

**Adapter captures run_id:**

```python
# In AgnoVercelAdapter._handle_event()
elif isinstance(event, RunCompletedEvent):
    if hasattr(event, "run_id") and event.run_id:
        self._agno_run_id = event.run_id
```

### 6. Message Retrieval - Load Sources

**File:** `app/main.py` (~line 772)

```python
@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(..., db: Session = Depends(get_db)):
    # ... existing session/message fetch ...

    message_ids = [m.id for m in filtered_messages if m.id]
    sources_by_message = load_sources_for_messages(db, message_ids)

    return [
        MessageResponse(
            ...,
            sources=[RAGSourceResponse(...) for s in sources_by_message.get(msg.id, [])]
                    if msg.role == "assistant" else None,
        )
        for msg in filtered_messages
    ]
```

### 7. Session Delete - Cleanup Sources

When deleting a session, also delete its message sources:

```python
@app.delete("/api/sessions/{session_id}")
async def delete_session(...):
    delete_sources_for_session(db, session_id)
    agent.delete_session(session_id=session_id)
```

---

## Files Modified

| File | Change |
|------|--------|
| `migrations/versions/007_create_message_sources.sql` | New migration |
| `app/services/message_sources_service.py` | New service (save/load/delete) |
| `app/adapters/vercel_stream.py` | Add `_collected_sources`, `_agno_run_id`, properties |
| `app/schemas/__init__.py` | Add `RAGSourceResponse`, update `MessageResponse` |
| `app/main.py` | Persist in chat endpoint, load in messages endpoint, cleanup on delete |

---

## Frontend Integration

**File:** `studybuddy-frontend/types/index.ts`
- Add `sources?: RAGSource[] | null` to `StoredMessage`

**File:** `studybuddy-frontend/hooks/useChat.ts`
- Add `sourcesMap` state to track sources per message ID
- Load sources from API response when fetching history
- Attach sources to messages when rendering

---

## Related Specs

- [RAG System](./rag-system.md)
- [Architecture](./architecture.md)
