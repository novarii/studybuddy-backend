# AI SDK UI → Next.js → Agno Bridge Plan

## Goal
- Provide a repeatable pattern for wiring the AI SDK UI (`useChat`) frontend to the existing FastAPI + Agno chat agent.
- Ensure course/user context is forwarded so the agent’s custom retriever can filter slides (owner scoped) and lectures (course scoped).
- Keep transport streaming-compatible so UI receives Agno’s streamed responses, tool calls, and metadata without format loss.

## Architecture Overview
1. **Client (Next.js app router page)**: Uses `useChat` + `DefaultChatTransport` to send chat messages to `/api/chat`. The transport injects auth/session headers, `courseId`, optional `documentId`/`lectureId`, and controls which messages are transmitted (typically only the latest user utterance).
2. **Next.js API Route (`app/api/chat/route.ts`)**: Acts as a proxy/bridge. Validates the signed-in user, enriches the payload with verified IDs, forwards the request to FastAPI, and streams the response back to AI SDK UI via `result.toUIMessageStreamResponse`.
3. **FastAPI Endpoint (e.g., `POST /api/agent/chat`)**: Invokes `create_chat_agent`, passes `owner_id`/`course_id`/`lecture_id` into `custom_retriever`, and streams output to the proxy.

## Implementation Steps

### 1. Client Transport Wiring
- Create a dedicated React client component (e.g., `components/chat/StudyBuddyChat.tsx`) that calls `useChat` with:
  - `transport: new DefaultChatTransport({ api: '/api/chat', headers: () => ({ Authorization: \`Bearer ${token}\` }) })`.
  - `prepareSendMessagesRequest` to:
    - Send only the last message for “submit user message”.
    - Include `courseId`, `documentId?`, `lectureId?`, `chatId`, and any UI-level metadata (`screenId`, `device` etc. if needed).
  - `resume: true` and `id` props if chat persistence is needed, per AI SDK UI resume example.
- Surface agent metadata (timestamps, token usage) via `message.metadata` helpers if we return them from Agno.

### 2. Next.js Route / Proxy
- Implement `app/api/chat/route.ts`:
  1. Read and validate JSON from the client: `{ id, message, trigger, courseId, documentId?, lectureId? }`.
  2. Resolve the authenticated user from cookies/session (e.g., NextAuth). Verify enrollment in the selected course.
  3. Construct a payload for FastAPI:
     ```ts
     const backendBody = {
       chatId: id,
       message,
       courseId,
       documentId,
       lectureId,
       // optionally prior server-side history if persisting
     };
     ```
  4. `fetch` the FastAPI endpoint with streaming enabled:
     ```ts
     const backendRes = await fetch(`${process.env.BACKEND_URL}/api/agent/chat`, {
       method: 'POST',
       headers,
       body: JSON.stringify(backendBody),
     });
     return new Response(backendRes.body, {
       headers: backendRes.headers,
     });
     ```
  5. Handle errors by mapping backend errors to AI SDK UI’s expected JSON error format and use `result.toUIMessageStreamResponse({ onError })` semantics.
- Add middleware for rate limiting and CSRF/cookie hardening if the API route is public.

### 3. FastAPI / Agno Endpoint
- Create `@router.post("/agent/chat")` that:
  - Requires authenticated service-to-service token from Next.js.
  - Loads any persisted chat history if needed (or simply uses the incoming last message).
  - Calls `create_chat_agent()` with:
    ```python
    agent = create_chat_agent()
    response = agent.run(
        input=message,
        user_id=str(user_uuid),
        metadata={"course_id": str(course_id), "document_id": ..., "lecture_id": ...},
    )
    ```
  - Ensures `owner_id` is passed to `custom_retriever` (via `agent.run(..., context={"owner_id": ...})` or by calling `get_relevant_docs_from_knowledge` with kwargs) so slide vectors are filtered, while lectures use only course filters.
  - Streams the response via `return Response(agent.stream(...), media_type="text/event-stream")` or any format compatible with AI SDK UI.
- If tool calls are planned, return them in the Agno output so AI SDK UI can render interactive tool parts (matching docs).

### 4. Persistence & Resume (optional)
- Store chat transcripts server-side keyed by `chatId` so `resume: true` can reload them, per AI SDK UI “message persistence” doc.
- Provide an endpoint `/api/chat/[id]` to fetch existing messages for SSR/initial load.

### 5. Persistence & Session Management (required)
- **Backend storage**:
  - Use Agno’s built-in session support by supplying a DB (e.g., `SqliteDb`, `PostgresDb`) to the agent so each chat run persists `AgentSession` rows automatically.
  - Enable `search_session_history=True` and `num_history_sessions=N` when we want the assistant to recall recent chats, referencing Agno’s sessions guide.
- **Session API**:
  - Implement FastAPI routes:
    - `GET /api/agent/sessions` → returns paginated list of sessions (ID, name, last updated, summary) filtered by authenticated user.
    - `DELETE /api/agent/sessions/{session_id}` → deletes both the Agno session rows and any cached chat history; requires user ownership.
    - `GET /api/agent/sessions/{session_id}` → returns stored messages so the client can rehydrate `useChat` initial messages.
- **Next.js UI**:
- Add a sidebar “Past chats” panel that loads sessions via `GET /api/chat/sessions` (proxying to FastAPI) and allows selection; when selected, pass `id` + `messages` down to `useChat`.
- Add delete controls that call the DELETE route and optimistically update the list.
- Support renaming by exposing `PATCH /api/agent/sessions/{session_id}` if desired (Agno session schemas allow `session_name` updates).
- **Deletion semantics**:
  - When a session is deleted client-side, ensure slide/lecture knowledge remains unaffected; only the conversation history is removed.
  - Consider soft-delete vs hard-delete; start with hard-delete unless requirements say otherwise.
  - Enforce one course per chat: persist `course_id` on each session row and always filter lists by both `user_id` and `course_id`. Creating a new chat while a course is selected should automatically tag the session with that course.

### 5. Observability & Safety
- Add structured logging across client proxy & backend (request IDs, user/course IDs, chunk IDs used).
- Monitor vector searches (slide vs lecture) to ensure they respect the owner/course scoping.
- Implement retry/back-pressure handling in Next.js for long-running streams (AI SDK docs allow `maxDuration` handling server-side).

## Assumptions & Ambiguities
- **Next.js Runtime**: Assuming Next.js App Router with Edge-compatible API routes; if using Pages Router or Node runtime, streaming APIs differ.
- **Auth Context**: Assuming we can extract a verified user ID/server session before proxying; not defined whether we use NextAuth, Auth0, or custom JWT.
- **Chat History Source**: Plan assumes either server persists history or client sends entire history; need confirmation on persistence requirements.
- **Tool Support**: Not clear whether we need client-side tools (e.g., location confirmation). Current plan keeps the proxy agnostic, but implementing tools may require additional UI components.
- **Metadata Schema**: Assumes Agno responses will include chunk metadata (lecture_id, slide_number) for UI display; if not, we must extend the backend to send it.
- **Error Handling Expectations**: Need confirmation on desired UX when backend/agent errors occur (toast vs inline message).
- **Session Lifecycle**: Need clarity on default retention, max sessions per user, and naming conventions (auto-generated vs user-provided) for listing/deleting chats.
