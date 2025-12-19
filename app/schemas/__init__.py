from __future__ import annotations

from datetime import datetime
from typing import Optional
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
