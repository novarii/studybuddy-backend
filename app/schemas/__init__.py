from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, constr

from ..database.models import DocumentStatus, LectureStatus


class LectureDownloadRequest(BaseModel):
    course_id: UUID
    panopto_url: constr(strip_whitespace=True, min_length=1)
    stream_url: constr(strip_whitespace=True, min_length=1)
    title: Optional[str] = None


class LectureDownloadResponse(BaseModel):
    lecture_id: UUID
    status: LectureStatus


class LectureStatusResponse(BaseModel):
    lecture_id: UUID
    status: LectureStatus
    error_message: Optional[str]
    duration_seconds: Optional[int]


class LectureDetailResponse(BaseModel):
    id: UUID
    course_id: UUID
    panopto_session_id: Optional[str]
    panopto_url: str
    stream_url: str
    title: Optional[str]
    duration_seconds: Optional[int]
    status: LectureStatus
    error_message: Optional[str]
    created_at: datetime
    updated_at: datetime


class DocumentUploadResponse(BaseModel):
    document_id: UUID
    course_id: UUID
    status: DocumentStatus


class DocumentDetailResponse(BaseModel):
    id: UUID
    course_id: UUID
    filename: str
    mime_type: str
    size_bytes: int
    page_count: Optional[int]
    status: DocumentStatus
    created_at: datetime
    updated_at: datetime


class CourseResponse(BaseModel):
    id: UUID
    code: str
    title: str
    instructor: Optional[str]


class LectureStatusListItem(BaseModel):
    id: UUID
    title: Optional[str]
    status: LectureStatus
    created_at: datetime
    updated_at: datetime


class ChatRequest(BaseModel):
    """Request body for POST /api/agent/chat."""

    message: constr(strip_whitespace=True, min_length=1)
    course_id: UUID
    document_id: Optional[UUID] = None
    lecture_id: Optional[UUID] = None
    session_id: Optional[str] = None


class CourseSyncResponse(BaseModel):
    """Response for POST /api/admin/courses/sync."""

    created: int
    updated: int
    unchanged: int
    deleted: int
    total: int
    terms: list[str]
    deletion_skipped: bool


class LectureAudioUploadMetadata(BaseModel):
    """Metadata for direct audio upload from browser extension."""

    session_id: constr(strip_whitespace=True, min_length=1)
    course_id: UUID
    title: Optional[str] = None
    duration: Optional[float] = None


class LectureAudioUploadResponse(BaseModel):
    """Response for POST /api/lectures/audio."""

    lecture_id: UUID
    status: LectureStatus
    created: bool


# --- Session Schemas ---


class SessionResponse(BaseModel):
    """Response for a single chat session."""

    session_id: str
    session_name: Optional[str] = None
    course_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class SessionListResponse(BaseModel):
    """Paginated list of sessions."""

    sessions: List[SessionResponse]
    total: int
    page: int
    limit: int


class RAGSourceResponse(BaseModel):
    """RAG source metadata for citation references."""

    source_id: str
    source_type: str
    content_preview: Optional[str] = None
    chunk_number: int
    document_id: Optional[str] = None
    slide_number: Optional[int] = None
    lecture_id: Optional[str] = None
    start_seconds: Optional[float] = None
    end_seconds: Optional[float] = None
    course_id: Optional[str] = None
    title: Optional[str] = None


class MessageResponse(BaseModel):
    """A single message in a chat session."""

    id: str
    role: str  # "user" | "assistant"
    content: str
    created_at: Optional[datetime] = None
    sources: Optional[List[RAGSourceResponse]] = None


class CreateSessionRequest(BaseModel):
    """Request body for POST /api/sessions."""

    course_id: UUID


class CreateSessionResponse(BaseModel):
    """Response for POST /api/sessions."""

    session_id: str


class GenerateTitleResponse(BaseModel):
    """Response for POST /api/sessions/{id}/generate-title."""

    session_name: Optional[str] = None
