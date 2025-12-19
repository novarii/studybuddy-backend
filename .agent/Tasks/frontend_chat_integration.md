# Frontend Chat Integration - Handoff Document

## Overview

The backend now exposes a streaming chat endpoint at `POST /api/agent/chat` that is compatible with **Vercel AI SDK v5**. This document describes how to integrate it with a Next.js frontend using `useChat`.

## Backend Endpoint

**URL:** `POST /api/agent/chat`

**Request Body:**
```json
{
  "message": "What is gradient descent?",
  "course_id": "uuid-of-course",
  "document_id": "uuid-of-document (optional)",
  "lecture_id": "uuid-of-lecture (optional)",
  "session_id": "chat-session-id (optional)"
}
```

**Response:** Server-Sent Events (SSE) stream with header `x-vercel-ai-ui-message-stream: v1`

**Authentication:** Requires Clerk JWT token in `Authorization: Bearer <token>` header

---

## Stream Event Types

The backend emits these event types in order:

| Event Type | When | Contains |
|------------|------|----------|
| `start` | Stream begins | `messageId` |
| `source-document` | Before text | `sourceId`, `mediaType`, `title` |
| `data-rag-source` | Before text | Full metadata (see below) |
| `text-start` | Text begins | `id` |
| `text-delta` | Each text chunk | `id`, `delta` |
| `text-end` | Text complete | `id` |
| `reasoning-start/delta/end` | If model reasons | Reasoning content |
| `tool-input-start` | Tool called | `toolCallId`, `toolName` |
| `tool-input-available` | Tool args ready | `toolCallId`, `toolName`, `input` |
| `tool-output-available` | Tool result | `toolCallId`, `output` |
| `finish` | Stream complete | - |
| `[DONE]` | SSE termination | - |

---

## RAG Source Metadata

The `data-rag-source` events contain full metadata for displaying source references:

```typescript
interface RAGSource {
  source_id: string;           // Unique ID like "slide-docId-5" or "lecture-lecId-120"
  source_type: 'slide' | 'lecture';
  content_preview: string;     // First 200 chars of content
  chunk_number: number;        // Citation number [1], [2], etc. for linking to response text

  // For slides
  document_id?: string;        // UUID of the PDF document
  slide_number?: number;       // 1-indexed slide number

  // For lectures
  lecture_id?: string;         // UUID of the lecture
  start_seconds?: number;      // Timestamp in lecture
  end_seconds?: number;

  // Common
  course_id?: string;
  owner_id?: string;
  title?: string;              // Document/lecture title
}
```

### Citation Linking

The agent cites sources using numbered brackets: `[1]`, `[2]`, `[3]`. The `chunk_number` field in each `RAGSource` corresponds to these citations, allowing the frontend to:

1. Display sources with their citation number: `[1] Slide 5 - Mitochondria`
2. Make citations in the response text clickable/hoverable
3. Highlight the corresponding source when a citation is clicked

**Example response text:**
```
The mitochondria is the powerhouse of the cell [1]. This process is also covered in the lecture [2].
```

**Corresponding sources:**
```json
[
  { "chunk_number": 1, "source_type": "slide", "slide_number": 5, ... },
  { "chunk_number": 2, "source_type": "lecture", "start_seconds": 120, ... }
]
```

---

## Next.js Implementation

### 1. Install Dependencies

```bash
npm install @ai-sdk/react ai
```

### 2. Create Type Definitions

```typescript
// types/chat.ts

export interface RAGSource {
  source_id: string;
  source_type: 'slide' | 'lecture';
  content_preview: string;
  chunk_number: number;        // Citation number for [1], [2], etc.
  document_id?: string;
  slide_number?: number;
  lecture_id?: string;
  start_seconds?: number;
  end_seconds?: number;
  course_id?: string;
  title?: string;
}

export interface ChatContext {
  courseId: string;
  documentId?: string;
  lectureId?: string;
}
```

### 3. Create Chat Component

```tsx
// components/chat/StudyBuddyChat.tsx
'use client';

import { useChat } from '@ai-sdk/react';
import { useState, useCallback } from 'react';
import type { RAGSource, ChatContext } from '@/types/chat';

interface StudyBuddyChatProps {
  context: ChatContext;
  authToken: string;
}

export function StudyBuddyChat({ context, authToken }: StudyBuddyChatProps) {
  const [input, setInput] = useState('');
  const [sources, setSources] = useState<RAGSource[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  const { messages, sendMessage, isLoading, error, stop } = useChat({
    api: process.env.NEXT_PUBLIC_API_URL + '/api/agent/chat',

    headers: {
      Authorization: `Bearer ${authToken}`,
    },

    // Transform request to match backend schema
    body: {
      course_id: context.courseId,
      document_id: context.documentId,
      lecture_id: context.lectureId,
    },

    // Handle streaming data events
    onData: (data) => {
      // data-rag-source contains full metadata
      if (data && typeof data === 'object' && 'source_id' in data) {
        setSources(prev => {
          // Dedupe by source_id
          if (prev.some(s => s.source_id === data.source_id)) return prev;
          return [...prev, data as RAGSource];
        });
        setIsSearching(false);
      }
    },

    // Reset sources when starting new message
    onResponse: () => {
      setSources([]);
      setIsSearching(true);
    },

    onFinish: () => {
      setIsSearching(false);
    },

    onError: (err) => {
      setIsSearching(false);
      console.error('Chat error:', err);
    },
  });

  const handleSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || isLoading) return;

    sendMessage({ content: input });
    setInput('');
  }, [input, isLoading, sendMessage]);

  return (
    <div className="flex flex-col h-full">
      {/* Sources Panel */}
      <SourcesPanel
        sources={sources}
        isSearching={isSearching}
        courseId={context.courseId}
      />

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {messages.map((message) => (
          <MessageBubble key={message.id} message={message} />
        ))}

        {isLoading && (
          <div className="flex items-center gap-2 text-gray-500">
            <span className="animate-pulse">Thinking...</span>
            <button onClick={stop} className="text-xs underline">Stop</button>
          </div>
        )}

        {error && (
          <div className="text-red-500 p-3 bg-red-50 rounded">
            Error: {error.message}
          </div>
        )}
      </div>

      {/* Input */}
      <form onSubmit={handleSubmit} className="p-4 border-t">
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about your course materials..."
            className="flex-1 p-3 border rounded-lg"
            disabled={isLoading}
          />
          <button
            type="submit"
            disabled={isLoading || !input.trim()}
            className="px-6 py-3 bg-blue-600 text-white rounded-lg disabled:opacity-50"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}
```

### 4. Create Sources Panel Component

```tsx
// components/chat/SourcesPanel.tsx

import type { RAGSource } from '@/types/chat';
import Link from 'next/link';

interface SourcesPanelProps {
  sources: RAGSource[];
  isSearching: boolean;
  courseId: string;
}

export function SourcesPanel({ sources, isSearching, courseId }: SourcesPanelProps) {
  if (!isSearching && sources.length === 0) return null;

  return (
    <div className="bg-blue-50 border-b p-3">
      {isSearching ? (
        <div className="flex items-center gap-2 text-blue-600">
          <span className="animate-spin">🔍</span>
          <span>Searching course materials...</span>
        </div>
      ) : (
        <>
          <h4 className="font-semibold text-sm text-blue-800 mb-2">
            Found {sources.length} relevant source{sources.length !== 1 ? 's' : ''}:
          </h4>
          <ul className="space-y-1">
            {sources.map((source) => (
              <SourceItem key={source.source_id} source={source} courseId={courseId} />
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function SourceItem({ source, courseId }: { source: RAGSource; courseId: string }) {
  if (source.source_type === 'slide') {
    return (
      <li className="text-sm" id={`source-${source.chunk_number}`}>
        <Link
          href={`/courses/${courseId}/documents/${source.document_id}?slide=${source.slide_number}`}
          className="text-blue-600 hover:underline flex items-center gap-1"
        >
          <span className="font-semibold text-gray-700">[{source.chunk_number}]</span>
          <span>📄</span>
          <span>
            {source.title || 'Document'} - Slide {source.slide_number}
          </span>
        </Link>
        {source.content_preview && (
          <p className="text-gray-500 text-xs truncate ml-8">
            {source.content_preview}
          </p>
        )}
      </li>
    );
  }

  if (source.source_type === 'lecture') {
    const timestamp = formatTimestamp(source.start_seconds);
    return (
      <li className="text-sm" id={`source-${source.chunk_number}`}>
        <Link
          href={`/courses/${courseId}/lectures/${source.lecture_id}?t=${source.start_seconds}`}
          className="text-blue-600 hover:underline flex items-center gap-1"
        >
          <span className="font-semibold text-gray-700">[{source.chunk_number}]</span>
          <span>🎥</span>
          <span>
            {source.title || 'Lecture'} @ {timestamp}
          </span>
        </Link>
        {source.content_preview && (
          <p className="text-gray-500 text-xs truncate ml-8">
            {source.content_preview}
          </p>
        )}
      </li>
    );
  }

  return null;
}

function formatTimestamp(seconds?: number): string {
  if (!seconds) return '0:00';
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, '0')}`;
}
```

### 5. Create Message Bubble Component

```tsx
// components/chat/MessageBubble.tsx

import type { Message } from '@ai-sdk/react';
import { memo } from 'react';
import ReactMarkdown from 'react-markdown';

interface MessageBubbleProps {
  message: Message;
}

export const MessageBubble = memo(function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === 'user';

  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[80%] rounded-lg p-4 ${
          isUser
            ? 'bg-blue-600 text-white'
            : 'bg-gray-100 text-gray-900'
        }`}
      >
        {message.parts.map((part, index) => {
          switch (part.type) {
            case 'text':
              return (
                <div key={index} className="prose prose-sm max-w-none">
                  <ReactMarkdown>{part.text}</ReactMarkdown>
                </div>
              );

            case 'reasoning':
              return (
                <details key={index} className="mt-2 text-sm opacity-70">
                  <summary className="cursor-pointer">View reasoning</summary>
                  <p className="mt-1 pl-2 border-l-2">{part.reasoning}</p>
                </details>
              );

            case 'tool-invocation':
              return (
                <div key={index} className="mt-2 p-2 bg-gray-200 rounded text-sm">
                  <div className="font-mono text-xs text-gray-600">
                    🔧 {part.toolInvocation.toolName}
                  </div>
                  {part.toolInvocation.state === 'result' && (
                    <pre className="mt-1 text-xs overflow-x-auto">
                      {JSON.stringify(part.toolInvocation.result, null, 2)}
                    </pre>
                  )}
                </div>
              );

            case 'source':
              // Native source parts - we handle these in SourcesPanel instead
              return null;

            default:
              return null;
          }
        })}
      </div>
    </div>
  );
});
```

### 6. Page Integration

```tsx
// app/courses/[courseId]/chat/page.tsx
import { auth } from '@clerk/nextjs';
import { StudyBuddyChat } from '@/components/chat/StudyBuddyChat';

interface Props {
  params: { courseId: string };
  searchParams: { documentId?: string; lectureId?: string };
}

export default async function ChatPage({ params, searchParams }: Props) {
  const { getToken } = auth();
  const token = await getToken();

  if (!token) {
    return <div>Please sign in to use chat.</div>;
  }

  return (
    <div className="h-screen">
      <StudyBuddyChat
        context={{
          courseId: params.courseId,
          documentId: searchParams.documentId,
          lectureId: searchParams.lectureId,
        }}
        authToken={token}
      />
    </div>
  );
}
```

---

## Optional: Next.js Proxy Route

If you prefer to proxy through Next.js (for CORS or to add server-side logic):

```typescript
// app/api/chat/route.ts
import { auth } from '@clerk/nextjs';

export const runtime = 'edge';

export async function POST(req: Request) {
  const { userId, getToken } = auth();

  if (!userId) {
    return new Response('Unauthorized', { status: 401 });
  }

  const token = await getToken();
  const body = await req.json();

  // Forward to backend
  const response = await fetch(process.env.BACKEND_URL + '/api/agent/chat', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${token}`,
    },
    body: JSON.stringify(body),
  });

  // Stream the response back
  return new Response(response.body, {
    headers: response.headers,
  });
}
```

Then update the chat component to use `/api/chat` instead of the direct backend URL.

---

## Environment Variables

```env
# .env.local
NEXT_PUBLIC_API_URL=http://localhost:8000
```

---

## Testing

1. Start the backend: `uv run uvicorn app.main:app --reload --port 8000`
2. Start the frontend: `npm run dev`
3. Navigate to `/courses/{courseId}/chat`
4. Send a message and verify:
   - Sources appear before text
   - Text streams incrementally
   - Sources link to correct documents/lectures

---

## Known Behaviors

1. **Sources emit before text** - The backend pre-retrieves sources and emits them immediately so the UI can show "Searching..." then "Found N sources" before the LLM response starts.

2. **Duplicate retrieval** - The agent's internal `custom_retriever` may retrieve the same sources again. The pre-retrieval is for UX (immediate feedback), the agent retrieval is for context injection.

3. **Tool calls** - If tools are added to the agent, they'll stream as `tool-invocation` parts. Render them in `MessageBubble`.

4. **Reasoning** - If the model provides reasoning (chain-of-thought), it streams as `reasoning` parts. Currently collapsed in a `<details>` element.

---

## Questions for Frontend Team

1. **Styling**: Should sources panel be collapsible? Fixed height with scroll?
2. **Deep links**: Confirm URL structure for linking to specific slides/timestamps
3. **Error handling**: Toast notifications vs inline errors?
4. **Session persistence**: Do we need chat history across page reloads?
5. **Mobile**: Any specific responsive requirements?

---

## Contact

Backend implementation by: [Your Name]
Files modified:
- `app/adapters/vercel_stream.py` - SSE adapter with `RAGSource` (includes `chunk_number`)
- `app/agents/context_formatter.py` - Lean context formatting with numbered citations
- `app/agents/chat_agent.py` - Agent with citation-aware instructions
- `app/main.py` - `/api/agent/chat` endpoint
- `app/schemas/__init__.py` - `ChatRequest` model
