# Chat Persistence - Backend Implementation Plan

## Overview

Implement chat session persistence using Agno's built-in storage system. This allows users to:
- Continue conversations across page reloads
- View chat history in sidebar
- Switch between multiple chat sessions per course

## What Agno Provides

Agno has built-in session persistence. When configured with a database:

| Feature | How | Docs |
|---------|-----|------|
| Auto-save messages | Pass `db=` to Agent | [Storage Intro](https://docs.agno.com/storage/introduction) |
| Load chat history | `agent.get_chat_history(session_id)` | [Chat History](https://docs.agno.com/basics/chat-history/agent/overview) |
| Multi-turn context | `add_history_to_context=True` | [Persisting Sessions](https://docs.agno.com/basics/sessions/persisting-sessions/overview) |
| List sessions | Query storage or API | [Session Management](https://docs.agno.com/basics/sessions/session-management) |
| Auto-generate titles | `set_session_name(autogenerate=True)` | [Session Management](https://docs.agno.com/basics/sessions/session-management) |

---

## Implementation Steps

### 1. Configure Agno Storage

**File:** `app/agents/chat_agent.py`

```python
from agno.db.postgres import PostgresDb
from app.core.config import settings

# Create shared database instance for Agno storage
def get_agno_db() -> PostgresDb:
    return PostgresDb(
        db_url=settings.DATABASE_URL,
        table_name="agno_sessions",  # Agno manages this table
    )
```

### 2. Update Agent Creation

**File:** `app/agents/chat_agent.py`

Modify `create_chat_agent()` to accept storage config:

```python
def create_chat_agent(
    *,
    instructions: Optional[str] = None,
    db: Optional[PostgresDb] = None,
) -> Agent:
    return Agent(
        name="StudyBuddyChatAgent",
        model=model,
        instructions=instructions or DEFAULT_INSTRUCTIONS,
        # Enable persistence
        db=db,
        add_history_to_context=True,
        num_history_runs=10,  # Include last 10 turns in LLM context
        # Existing config
        search_knowledge=False,
        add_knowledge_to_context=False,
        markdown=True,
    )
```

### 3. Update Chat Endpoint

**File:** `app/main.py`

Modify `POST /api/agent/chat` to use session persistence:

```python
from app.agents.chat_agent import create_chat_agent, get_agno_db

class ChatRequest(BaseModel):
    message: constr(strip_whitespace=True, min_length=1)
    course_id: UUID
    session_id: Optional[str] = None  # If None, Agno auto-generates
    document_id: Optional[UUID] = None
    lecture_id: Optional[UUID] = None

@app.post("/api/agent/chat")
async def chat(
    payload: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_user),
):
    async def generate_stream():
        adapter = AgnoVercelAdapter()
        db = get_agno_db()

        # Create agent with persistence
        agent = create_chat_agent(db=db)

        # RAG retrieval (existing logic)
        raw_references = retrieve_documents(...)
        formatted = format_retrieval_context(raw_references, ...)
        pre_sources = adapter.extract_sources_from_references(...)

        # Build message with RAG context
        user_message = f"{payload.message}\n\n<references>\n{formatted.model_context}\n</references>"

        # Run agent with session persistence
        # Agno automatically:
        # - Loads previous messages if session_id exists
        # - Saves this message + response after completion
        stream = agent.run(
            input=user_message,
            stream=True,
            stream_events=True,
            session_id=payload.session_id,  # Agno handles persistence
            user_id=str(current_user.user_id),  # User isolation
        )

        # Stream response (existing SSE logic)
        for chunk in adapter.transform_stream_sync(stream, pre_retrieved_sources=pre_sources):
            yield chunk

    return StreamingResponse(generate_stream(), headers=get_vercel_stream_headers())
```

### 4. Add Session Endpoints

**File:** `app/main.py`

```python
from agno.db.postgres import PostgresDb

# --- Session Schemas ---

class SessionResponse(BaseModel):
    session_id: str
    session_name: Optional[str] = None
    course_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

class SessionListResponse(BaseModel):
    sessions: List[SessionResponse]
    total: int
    page: int
    limit: int

class MessageResponse(BaseModel):
    id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: Optional[datetime] = None

class CreateSessionRequest(BaseModel):
    course_id: UUID

class CreateSessionResponse(BaseModel):
    session_id: str


# --- Endpoints ---

@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    course_id: Optional[UUID] = None,
    page: int = 1,
    limit: int = 20,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """List user's chat sessions, optionally filtered by course."""
    db = get_agno_db()

    # Query Agno's session storage
    # Option 1: Use Agno's built-in methods
    sessions = db.get_sessions(
        user_id=str(current_user.user_id),
        limit=limit,
        offset=(page - 1) * limit,
    )

    # Option 2: Direct SQL query for more control
    # (Agno stores sessions in the table specified in PostgresDb)

    # Filter by course_id if provided (stored in session metadata)
    if course_id:
        sessions = [s for s in sessions if s.metadata.get("course_id") == str(course_id)]

    return SessionListResponse(
        sessions=[
            SessionResponse(
                session_id=s.session_id,
                session_name=s.session_name,
                course_id=s.metadata.get("course_id"),
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in sessions
        ],
        total=len(sessions),  # TODO: Get actual count
        page=page,
        limit=limit,
    )


@app.post("/api/sessions", response_model=CreateSessionResponse)
async def create_session(
    payload: CreateSessionRequest,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Create a new chat session for a course."""
    import uuid

    session_id = str(uuid.uuid4())

    # Initialize session in Agno by running a "system" initialization
    # Or just return the ID - Agno will create on first message
    db = get_agno_db()

    # Store course_id in session metadata
    # This happens automatically on first agent.run() with metadata param
    # Or we can pre-create the session:
    db.create_session(
        session_id=session_id,
        user_id=str(current_user.user_id),
        metadata={"course_id": str(payload.course_id)},
    )

    return CreateSessionResponse(session_id=session_id)


@app.get("/api/sessions/{session_id}/messages", response_model=List[MessageResponse])
async def get_session_messages(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Get all messages in a session."""
    db = get_agno_db()
    agent = create_chat_agent(db=db)

    # Verify session belongs to user
    session = db.get_session(session_id=session_id)
    if not session or session.user_id != str(current_user.user_id):
        raise HTTPException(status_code=404, detail="Session not found")

    # Get chat history (user + assistant messages only)
    messages = agent.get_chat_history(session_id=session_id)

    return [
        MessageResponse(
            id=msg.id,
            role=msg.role,
            content=msg.content,
            created_at=msg.created_at,
        )
        for msg in messages
    ]


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Delete a chat session and all its messages."""
    db = get_agno_db()

    # Verify ownership
    session = db.get_session(session_id=session_id)
    if not session or session.user_id != str(current_user.user_id):
        raise HTTPException(status_code=404, detail="Session not found")

    db.delete_session(session_id=session_id)
    return {"status": "deleted"}


@app.post("/api/sessions/{session_id}/generate-title")
async def generate_session_title(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Auto-generate a title for the session based on conversation content."""
    db = get_agno_db()
    agent = create_chat_agent(db=db)

    # Verify ownership
    session = db.get_session(session_id=session_id)
    if not session or session.user_id != str(current_user.user_id):
        raise HTTPException(status_code=404, detail="Session not found")

    # Agno auto-generates title from first few messages
    agent.set_session_name(session_id=session_id, autogenerate=True)

    updated_session = db.get_session(session_id=session_id)
    return {"session_name": updated_session.session_name}
```

### 5. Store Course ID in Session Metadata

When creating/running a session, store the `course_id` in metadata so we can filter sessions by course:

```python
# In the chat endpoint, pass metadata to agent.run()
stream = agent.run(
    input=user_message,
    stream=True,
    session_id=payload.session_id,
    user_id=str(current_user.user_id),
    metadata={"course_id": str(payload.course_id)},  # Stored with session
)
```

---

## API Summary

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/agent/chat` | Send message (now with session_id) |
| `GET` | `/api/sessions` | List user's sessions |
| `POST` | `/api/sessions` | Create new session |
| `GET` | `/api/sessions/{id}/messages` | Get session messages |
| `DELETE` | `/api/sessions/{id}` | Delete session |
| `POST` | `/api/sessions/{id}/generate-title` | Auto-generate title |

---

## Database Considerations

Agno automatically creates and manages its session table. The schema includes:

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | Unique identifier |
| `user_id` | `str` | User who owns the session |
| `session_name` | `str` | Human-readable title |
| `session_data` | `dict` | Session state |
| `metadata` | `dict` | Custom data (course_id, etc.) |
| `runs` | `list` | All messages/runs |
| `created_at` | `timestamp` | Creation time |
| `updated_at` | `timestamp` | Last update |

No manual migrations needed - Agno handles table creation.

---

## Message Format for Frontend

The frontend uses AI SDK's `UIMessage` format. When returning messages from `GET /sessions/{id}/messages`, convert to:

```json
{
  "id": "msg-uuid",
  "role": "user",
  "content": "What is gradient descent?",
  "created_at": "2025-01-15T10:30:00Z"
}
```

The frontend will convert this to `UIMessage` parts format.

---

## Testing

```bash
# Create a session
curl -X POST http://localhost:8000/api/sessions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"course_id": "uuid-here"}'

# Send a message
curl -X POST http://localhost:8000/api/agent/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello", "course_id": "uuid", "session_id": "session-uuid"}'

# List sessions
curl http://localhost:8000/api/sessions \
  -H "Authorization: Bearer $TOKEN"

# Get messages
curl http://localhost:8000/api/sessions/session-uuid/messages \
  -H "Authorization: Bearer $TOKEN"
```

---

## Files to Modify

| File | Changes |
|------|---------|
| `app/agents/chat_agent.py` | Add `get_agno_db()`, update `create_chat_agent()` |
| `app/main.py` | Update chat endpoint, add session endpoints |
| `app/schemas/__init__.py` | Add session request/response schemas |
| `requirements.txt` / `pyproject.toml` | Ensure `agno[postgres]` installed |

---

## Open Questions

1. **Session naming**: Auto-generate after first response, and let user change it later if they want to.
2. **Session limits**: Max sessions per user/course? -- keep unlimited for now
3. **Message editing**: Support edit/delete individual messages? -- yes later
4. **Export**: Allow exporting chat history? no
