"""
Adapter that converts Agno RunEvent stream to Vercel AI SDK v5 SSE format.

This module provides the AgnoVercelAdapter class which transforms streaming events
from Agno agents into the Server-Sent Events (SSE) format expected by the Vercel
AI SDK v5 useChat hook.

Vercel v5 Protocol Summary:
- Uses Server-Sent Events (SSE) format: `data: {json}\n\n`
- Text: text-start -> text-delta* -> text-end (each has id)
- Reasoning: reasoning-start -> reasoning-delta* -> reasoning-end
- Tools: tool-input-start -> tool-input-available -> tool-output-available
- Sources: source-document, source-url, or custom data-* types
- Lifecycle: start -> ... -> finish -> [DONE]

Required Header: x-vercel-ai-ui-message-stream: v1
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from typing import Any, AsyncIterator, Dict, Iterator, List, Optional, Union
from uuid import uuid4

from agno.run.agent import (
    RunEvent,
    RunStartedEvent,
    RunContentEvent,
    RunContentCompletedEvent,
    RunCompletedEvent,
    RunErrorEvent,
    ReasoningStartedEvent,
    ReasoningStepEvent,
    ReasoningCompletedEvent,
    ToolCallStartedEvent,
    ToolCallCompletedEvent,
    BaseAgentRunEvent,
    CustomEvent,
)

logger = logging.getLogger(__name__)


@dataclass
class RAGSource:
    """Structured RAG source with full metadata for frontend display."""

    source_id: str
    source_type: str  # "slide" or "lecture"
    content_preview: str
    # Citation number for correlating with [1], [2], etc. in response
    chunk_number: Optional[int] = None
    # Slide-specific
    document_id: Optional[str] = None
    slide_number: Optional[int] = None
    # Lecture-specific
    lecture_id: Optional[str] = None
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    # Common
    course_id: Optional[str] = None
    owner_id: Optional[str] = None
    title: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict, excluding None values."""
        return {k: v for k, v in asdict(self).items() if v is not None}


class AgnoVercelAdapter:
    """
    Converts Agno RunEvent iterator to Vercel AI SDK v5 SSE format.

    This adapter handles both sync and async iteration over Agno events,
    transforming them into the SSE format expected by useChat.
    """

    def __init__(
        self,
        *,
        message_id: Optional[str] = None,
        emit_sources_before_text: bool = True,
    ):
        """
        Initialize the adapter.

        Args:
            message_id: Optional message ID. If not provided, a UUID will be generated.
            emit_sources_before_text: If True, emit sources before the first text delta.
        """
        self.message_id = message_id or str(uuid4())
        self.emit_sources_before_text = emit_sources_before_text
        self._text_block_id: Optional[str] = None
        self._reasoning_block_id: Optional[str] = None
        self._text_started = False
        self._reasoning_started = False
        self._sources_emitted = False
        self._collected_sources: List[RAGSource] = []
        self._agno_run_id: Optional[str] = None

    def _sse_event(self, data: Dict[str, Any]) -> str:
        """Format a single SSE event line."""
        return f"data: {json.dumps(data)}\n\n"

    def _emit_start(self) -> str:
        """Emit the message start event."""
        return self._sse_event({"type": "start", "messageId": self.message_id})

    def _emit_finish(self) -> str:
        """Emit the finish event."""
        return self._sse_event({"type": "finish"})

    def _emit_done(self) -> str:
        """Emit the stream termination marker."""
        return "data: [DONE]\n\n"

    def _emit_error(self, error_text: str) -> str:
        """Emit an error event."""
        return self._sse_event({"type": "error", "errorText": error_text})

    def _emit_text_start(self) -> str:
        """Emit text-start event."""
        self._text_block_id = str(uuid4())
        return self._sse_event({"type": "text-start", "id": self._text_block_id})

    def _emit_text_delta(self, delta: str) -> str:
        """Emit text-delta event."""
        return self._sse_event({
            "type": "text-delta",
            "id": self._text_block_id,
            "delta": delta,
        })

    def _emit_text_end(self) -> str:
        """Emit text-end event."""
        return self._sse_event({"type": "text-end", "id": self._text_block_id})

    def _emit_reasoning_start(self) -> str:
        """Emit reasoning-start event."""
        self._reasoning_block_id = str(uuid4())
        return self._sse_event({"type": "reasoning-start", "id": self._reasoning_block_id})

    def _emit_reasoning_delta(self, delta: str) -> str:
        """Emit reasoning-delta event."""
        return self._sse_event({
            "type": "reasoning-delta",
            "id": self._reasoning_block_id,
            "delta": delta,
        })

    def _emit_reasoning_end(self) -> str:
        """Emit reasoning-end event."""
        return self._sse_event({"type": "reasoning-end", "id": self._reasoning_block_id})

    def _emit_tool_input_start(self, tool_call_id: str, tool_name: str) -> str:
        """Emit tool-input-start event."""
        return self._sse_event({
            "type": "tool-input-start",
            "toolCallId": tool_call_id,
            "toolName": tool_name,
        })

    def _emit_tool_input_available(
        self, tool_call_id: str, tool_name: str, input_data: Dict[str, Any]
    ) -> str:
        """Emit tool-input-available event."""
        return self._sse_event({
            "type": "tool-input-available",
            "toolCallId": tool_call_id,
            "toolName": tool_name,
            "input": input_data,
        })

    def _emit_tool_output_available(self, tool_call_id: str, output: Any) -> str:
        """Emit tool-output-available event."""
        return self._sse_event({
            "type": "tool-output-available",
            "toolCallId": tool_call_id,
            "output": output,
        })

    def _emit_source_document(self, source: RAGSource) -> str:
        """Emit source-document event (native Vercel format)."""
        return self._sse_event({
            "type": "source-document",
            "sourceId": source.source_id,
            "mediaType": source.source_type,
            "title": source.title or f"{source.source_type.title()} Source",
        })

    def _emit_rag_source(self, source: RAGSource) -> str:
        """Emit custom data-rag-source event with full metadata."""
        return self._sse_event({
            "type": "data-rag-source",
            "data": source.to_dict(),
        })

    def _emit_all_sources(self, sources: List[RAGSource]) -> str:
        """Emit all source events (both native and custom formats)."""
        self._collected_sources.extend(sources)
        output = ""
        for source in sources:
            # Emit native source-document for UI compatibility
            output += self._emit_source_document(source)
            # Emit custom data-rag-source with full metadata
            output += self._emit_rag_source(source)
        return output

    @property
    def collected_sources(self) -> List[RAGSource]:
        """Return all sources collected during streaming."""
        return self._collected_sources

    @property
    def agno_run_id(self) -> Optional[str]:
        """Return the Agno run_id captured from RunCompletedEvent."""
        return self._agno_run_id

    def extract_sources_from_references(
        self, references: Optional[List[Union[Dict[str, Any], str]]]
    ) -> List[RAGSource]:
        """
        Extract RAGSource objects from retriever output.

        Args:
            references: List of reference dicts from retrieve_documents() or
                format_retrieval_context(). Each dict has: content, name (optional),
                metadata (optional), chunk_number (optional - set by context_formatter).

        Returns:
            List of RAGSource objects with full metadata.
        """
        if not references:
            return []

        sources: List[RAGSource] = []
        for idx, ref in enumerate(references, start=1):
            if isinstance(ref, str):
                # Plain string reference
                sources.append(RAGSource(
                    source_id=str(uuid4()),
                    source_type="unknown",
                    content_preview=ref[:200] if ref else "",
                    chunk_number=idx,
                ))
                continue

            metadata = ref.get("metadata", {})
            content = ref.get("content", "")
            # Use chunk_number from enriched reference if present, else fallback to index
            chunk_number = ref.get("chunk_number", idx)

            # Determine source type based on metadata
            if metadata.get("document_id"):
                source_type = "slide"
                doc_id = metadata.get("document_id")
                slide_num = metadata.get("slide_number", 0)
                source_id = f"slide-{doc_id}-{slide_num}"
            elif metadata.get("lecture_id"):
                source_type = "lecture"
                lec_id = metadata.get("lecture_id")
                start_sec = int(metadata.get("start_seconds", 0))
                source_id = f"lecture-{lec_id}-{start_sec}"
            else:
                source_type = "unknown"
                source_id = str(uuid4())

            sources.append(RAGSource(
                source_id=source_id,
                source_type=source_type,
                content_preview=content[:200] if content else "",
                chunk_number=chunk_number,
                document_id=metadata.get("document_id"),
                slide_number=metadata.get("slide_number"),
                lecture_id=metadata.get("lecture_id"),
                start_seconds=metadata.get("start_seconds"),
                end_seconds=metadata.get("end_seconds"),
                course_id=metadata.get("course_id"),
                owner_id=metadata.get("owner_id"),
                title=ref.get("name"),
            ))

        return sources

    def _extract_sources_from_message_references(
        self, references: Optional[List[Any]]
    ) -> List[RAGSource]:
        """
        Extract sources from Agno MessageReferences objects.

        Args:
            references: List of MessageReferences from RunContentEvent

        Returns:
            List of RAGSource objects.
        """
        if not references:
            return []

        all_sources: List[RAGSource] = []
        for msg_ref in references:
            # MessageReferences has .references attribute containing the actual docs
            if hasattr(msg_ref, "references") and msg_ref.references:
                all_sources.extend(
                    self.extract_sources_from_references(msg_ref.references)
                )
            elif hasattr(msg_ref, "model_dump"):
                # Pydantic model - try to extract
                ref_dict = msg_ref.model_dump()
                if "references" in ref_dict and ref_dict["references"]:
                    all_sources.extend(
                        self.extract_sources_from_references(ref_dict["references"])
                    )
        return all_sources

    def _handle_event(self, event: BaseAgentRunEvent) -> Iterator[str]:
        """
        Handle a single Agno event and yield SSE chunks.

        Args:
            event: An Agno BaseAgentRunEvent subclass instance.

        Yields:
            SSE-formatted strings.
        """
        if isinstance(event, RunStartedEvent):
            # Already emitted start in transform_stream
            pass

        elif isinstance(event, RunContentEvent):
            # Handle references/sources if not yet emitted
            if (
                not self._sources_emitted
                and self.emit_sources_before_text
                and event.references
            ):
                sources = self._extract_sources_from_message_references(event.references)
                if sources:
                    yield self._emit_all_sources(sources)
                    self._sources_emitted = True

            # Handle reasoning content
            if event.reasoning_content:
                if not self._reasoning_started:
                    yield self._emit_reasoning_start()
                    self._reasoning_started = True
                yield self._emit_reasoning_delta(event.reasoning_content)

            # Handle text content
            if event.content:
                if not self._text_started:
                    yield self._emit_text_start()
                    self._text_started = True
                yield self._emit_text_delta(str(event.content))

        elif isinstance(event, RunContentCompletedEvent):
            # Content block completed, but stream continues
            pass

        elif isinstance(event, RunCompletedEvent):
            # Capture the Agno run_id for source persistence
            if hasattr(event, "run_id") and event.run_id:
                self._agno_run_id = event.run_id

            # Handle references if not yet emitted
            if event.references and not self._sources_emitted:
                sources = self._extract_sources_from_message_references(event.references)
                if sources:
                    yield self._emit_all_sources(sources)
                    self._sources_emitted = True

            # Final content (if any remaining)
            if event.content and not self._text_started:
                yield self._emit_text_start()
                self._text_started = True
                yield self._emit_text_delta(str(event.content))

        elif isinstance(event, RunErrorEvent):
            error_text = str(event.content) if event.content else "An error occurred"
            yield self._emit_error(error_text)

        elif isinstance(event, ReasoningStartedEvent):
            if not self._reasoning_started:
                yield self._emit_reasoning_start()
                self._reasoning_started = True

        elif isinstance(event, ReasoningStepEvent):
            if not self._reasoning_started:
                yield self._emit_reasoning_start()
                self._reasoning_started = True
            if hasattr(event, "reasoning_content") and event.reasoning_content:
                yield self._emit_reasoning_delta(event.reasoning_content)

        elif isinstance(event, ReasoningCompletedEvent):
            if self._reasoning_started:
                yield self._emit_reasoning_end()
                self._reasoning_started = False

        elif isinstance(event, ToolCallStartedEvent):
            if event.tool:
                yield self._emit_tool_input_start(
                    tool_call_id=event.tool.tool_call_id or str(uuid4()),
                    tool_name=event.tool.tool_name or "unknown_tool",
                )

        elif isinstance(event, ToolCallCompletedEvent):
            if event.tool:
                tool_call_id = event.tool.tool_call_id or str(uuid4())
                tool_name = event.tool.tool_name or "unknown_tool"

                # Emit input available
                yield self._emit_tool_input_available(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    input_data=event.tool.tool_args or {},
                )

                # Emit output available
                yield self._emit_tool_output_available(
                    tool_call_id=tool_call_id,
                    output=event.tool.result,
                )

        elif isinstance(event, CustomEvent):
            # Handle RAGSourcesEvent (custom event with sources attribute)
            if hasattr(event, "sources") and event.sources:
                sources = self.extract_sources_from_references(event.sources)
                if sources:
                    yield self._emit_all_sources(sources)
                    self._sources_emitted = True

    def transform_stream_sync(
        self,
        agno_stream: Iterator[BaseAgentRunEvent],
        *,
        pre_retrieved_sources: Optional[List[RAGSource]] = None,
    ) -> Iterator[str]:
        """
        Transform synchronous Agno event stream to Vercel AI SDK v5 SSE format.

        Args:
            agno_stream: Iterator of Agno BaseAgentRunEvent objects.
            pre_retrieved_sources: Optional sources retrieved before streaming starts.

        Yields:
            SSE-formatted strings ready for HTTP response.
        """
        # Emit message start
        yield self._emit_start()

        # Emit pre-retrieved sources if provided
        if pre_retrieved_sources and self.emit_sources_before_text:
            yield self._emit_all_sources(pre_retrieved_sources)
            self._sources_emitted = True

        try:
            for event in agno_stream:
                yield from self._handle_event(event)

            # Ensure text block is closed
            if self._text_started:
                yield self._emit_text_end()

            # Ensure reasoning block is closed
            if self._reasoning_started:
                yield self._emit_reasoning_end()

            # Emit finish and done
            yield self._emit_finish()
            yield self._emit_done()

        except Exception as e:
            logger.exception("Error during stream transformation")
            yield self._emit_error(str(e))
            yield self._emit_done()

    async def transform_stream(
        self,
        agno_stream: AsyncIterator[BaseAgentRunEvent],
        *,
        pre_retrieved_sources: Optional[List[RAGSource]] = None,
    ) -> AsyncIterator[str]:
        """
        Transform async Agno event stream to Vercel AI SDK v5 SSE format.

        Args:
            agno_stream: Async iterator of Agno BaseAgentRunEvent objects.
            pre_retrieved_sources: Optional sources retrieved before streaming starts.

        Yields:
            SSE-formatted strings ready for HTTP response.
        """
        # Emit message start
        yield self._emit_start()

        # Emit pre-retrieved sources if provided
        if pre_retrieved_sources and self.emit_sources_before_text:
            yield self._emit_all_sources(pre_retrieved_sources)
            self._sources_emitted = True

        try:
            async for event in agno_stream:
                for chunk in self._handle_event(event):
                    yield chunk

            # Ensure text block is closed
            if self._text_started:
                yield self._emit_text_end()

            # Ensure reasoning block is closed
            if self._reasoning_started:
                yield self._emit_reasoning_end()

            # Emit finish and done
            yield self._emit_finish()
            yield self._emit_done()

        except Exception as e:
            logger.exception("Error during stream transformation")
            yield self._emit_error(str(e))
            yield self._emit_done()


def get_vercel_stream_headers() -> Dict[str, str]:
    """Return required headers for Vercel AI SDK v5 stream responses."""
    return {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "x-vercel-ai-ui-message-stream": "v1",
    }
