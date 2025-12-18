"""Context formatter for lean model context with numbered citations.

This module separates what the model sees (clean numbered text) from what
the client receives (rich metadata for UI).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Union

ReferenceType = Union[Dict[str, Any], str]


@dataclass
class FormattedContext:
    """Separated model context and client sources."""

    model_context: str
    """Lean numbered text for LLM consumption."""

    client_sources: list[dict[str, Any]]
    """Rich metadata for frontend, includes chunk_number."""

    chunk_map: dict[int, dict[str, Any]] = field(default_factory=dict)
    """Maps chunk number -> full reference for post-processing."""


def _format_timestamp(seconds: float) -> str:
    """Format seconds into MM:SS or HH:MM:SS."""
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _get_source_hint(ref: dict[str, Any]) -> str:
    """Generate a minimal source hint for model context."""
    meta = ref.get("metadata", {})

    if meta.get("document_id"):
        slide_num = meta.get("slide_number", "?")
        return f"Slide {slide_num}"

    if meta.get("lecture_id"):
        start = meta.get("start_seconds", 0)
        return f"Lecture @{_format_timestamp(start)}"

    return "Source"


def _extract_sort_key_slide(ref: dict[str, Any]) -> tuple[str, int]:
    """Sort key for slides: (document_id, slide_number)."""
    meta = ref.get("metadata", {})
    return (
        meta.get("document_id", ""),
        meta.get("slide_number", 0),
    )


def _extract_sort_key_lecture(ref: dict[str, Any]) -> tuple[str, float]:
    """Sort key for lectures: (lecture_id, start_seconds)."""
    meta = ref.get("metadata", {})
    return (
        meta.get("lecture_id", ""),
        meta.get("start_seconds", 0.0),
    )


def _order_chunks(
    references: list[dict[str, Any]],
    order_by: Literal["relevance", "chronological"],
) -> list[dict[str, Any]]:
    """Order chunks by specified strategy.

    Args:
        references: Raw references from retrieval.
        order_by: Ordering strategy.
            - "relevance": Keep original order (vector similarity ranking).
            - "chronological": Order slides by number, lectures by timestamp.

    Returns:
        Ordered list of references.
    """
    if order_by == "relevance":
        return references

    slides = []
    lectures = []
    other = []

    for ref in references:
        if not isinstance(ref, dict):
            other.append(ref)
            continue

        meta = ref.get("metadata", {})
        if meta.get("document_id"):
            slides.append(ref)
        elif meta.get("lecture_id"):
            lectures.append(ref)
        else:
            other.append(ref)

    slides.sort(key=_extract_sort_key_slide)
    lectures.sort(key=_extract_sort_key_lecture)

    return slides + lectures + other


def _build_model_context(ordered_refs: list[dict[str, Any]]) -> str:
    """Build numbered context string for LLM - content only, minimal metadata."""
    lines = []
    for i, ref in enumerate(ordered_refs, start=1):
        if isinstance(ref, str):
            content = ref.strip()
            source_hint = "Source"
        else:
            content = ref.get("content", "").strip()
            source_hint = _get_source_hint(ref)

        if content:
            lines.append(f"[{i}] ({source_hint}) {content}")

    return "\n\n".join(lines)


def _enrich_client_sources(
    ordered_refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add chunk_number to each reference for client consumption."""
    enriched = []
    for i, ref in enumerate(ordered_refs, start=1):
        if isinstance(ref, dict):
            enriched_ref = dict(ref)
            enriched_ref["chunk_number"] = i
            enriched.append(enriched_ref)
        else:
            enriched.append({"content": str(ref), "chunk_number": i})
    return enriched


def _build_chunk_map(ordered_refs: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Build mapping from chunk number to full reference."""
    chunk_map = {}
    for i, ref in enumerate(ordered_refs, start=1):
        if isinstance(ref, dict):
            chunk_map[i] = ref
        else:
            chunk_map[i] = {"content": str(ref)}
    return chunk_map


def format_retrieval_context(
    references: list[ReferenceType],
    *,
    order_by: Literal["relevance", "chronological"] = "chronological",
) -> FormattedContext:
    """Transform raw references into lean model context and rich client sources.

    Args:
        references: Raw references from retrieve_documents().
        order_by: Ordering strategy for chunks.

    Returns:
        FormattedContext with:
        - model_context: Numbered plain text for the LLM.
        - client_sources: Full metadata with chunk_number for frontend.
        - chunk_map: Mapping for resolving citations post-response.
    """
    normalized: list[dict[str, Any]] = []
    for ref in references:
        if isinstance(ref, dict):
            normalized.append(ref)
        else:
            normalized.append({"content": str(ref)})

    ordered = _order_chunks(normalized, order_by)
    model_context = _build_model_context(ordered)
    client_sources = _enrich_client_sources(ordered)
    chunk_map = _build_chunk_map(ordered)

    return FormattedContext(
        model_context=model_context,
        client_sources=client_sources,
        chunk_map=chunk_map,
    )


__all__ = ["FormattedContext", "format_retrieval_context"]
