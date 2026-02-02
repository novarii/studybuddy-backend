"""
Service for persisting and loading RAG sources for chat messages.

This enables citations to work when loading message history (page refresh, session switch).
"""

from __future__ import annotations

from typing import List, Dict, Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..adapters.vercel_stream import RAGSource


def save_message_sources(
    db: Session,
    *,
    message_id: str,
    session_id: str,
    sources: List[RAGSource],
) -> None:
    """
    Persist RAG sources for an assistant message.

    Uses INSERT ... ON CONFLICT DO NOTHING to handle duplicates gracefully.
    """
    if not sources:
        return

    for source in sources:
        db.execute(
            text("""
                INSERT INTO ai.message_sources (
                    message_id, session_id, source_id, source_type, chunk_number,
                    content_preview, document_id, slide_number, lecture_id,
                    start_seconds, end_seconds, course_id, owner_id, title
                ) VALUES (
                    :message_id, :session_id, :source_id, :source_type, :chunk_number,
                    :content_preview, :document_id, :slide_number, :lecture_id,
                    :start_seconds, :end_seconds, :course_id, :owner_id, :title
                )
                ON CONFLICT (message_id, source_id) DO NOTHING
            """),
            {
                "message_id": message_id,
                "session_id": session_id,
                "source_id": source.source_id,
                "source_type": source.source_type,
                "chunk_number": source.chunk_number or 0,
                "content_preview": source.content_preview,
                "document_id": _to_uuid(source.document_id),
                "slide_number": source.slide_number,
                "lecture_id": _to_uuid(source.lecture_id),
                "start_seconds": source.start_seconds,
                "end_seconds": source.end_seconds,
                "course_id": _to_uuid(source.course_id),
                "owner_id": _to_uuid(source.owner_id),
                "title": source.title,
            },
        )
    db.commit()


def load_sources_for_messages(
    db: Session,
    message_ids: List[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Load sources for multiple messages.

    Returns a dict mapping message_id -> list of source dicts.
    """
    if not message_ids:
        return {}

    result = db.execute(
        text("""
            SELECT
                message_id, source_id, source_type, chunk_number,
                content_preview, document_id, slide_number, lecture_id,
                start_seconds, end_seconds, course_id, owner_id, title
            FROM ai.message_sources
            WHERE message_id = ANY(:message_ids)
            ORDER BY chunk_number ASC
        """),
        {"message_ids": message_ids},
    )

    sources_by_message: Dict[str, List[Dict[str, Any]]] = {}
    for row in result.mappings():
        msg_id = row["message_id"]
        if msg_id not in sources_by_message:
            sources_by_message[msg_id] = []
        sources_by_message[msg_id].append({
            "source_id": row["source_id"],
            "source_type": row["source_type"],
            "chunk_number": row["chunk_number"],
            "content_preview": row["content_preview"],
            "document_id": str(row["document_id"]) if row["document_id"] else None,
            "slide_number": row["slide_number"],
            "lecture_id": str(row["lecture_id"]) if row["lecture_id"] else None,
            "start_seconds": row["start_seconds"],
            "end_seconds": row["end_seconds"],
            "course_id": str(row["course_id"]) if row["course_id"] else None,
            "title": row["title"],
        })

    return sources_by_message


def delete_sources_for_session(db: Session, session_id: str) -> int:
    """
    Delete all sources for a session.

    Returns the number of deleted rows.
    """
    result = db.execute(
        text("DELETE FROM ai.message_sources WHERE session_id = :session_id"),
        {"session_id": session_id},
    )
    db.commit()
    return result.rowcount


def _to_uuid(value: str | None) -> UUID | None:
    """Convert string to UUID, returning None if empty or None."""
    if not value:
        return None
    try:
        return UUID(value)
    except (ValueError, TypeError):
        return None
