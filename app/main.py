from __future__ import annotations

from uuid import UUID

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser, require_user
from .config import settings
from .db import get_db
from .documents_service import DocumentsService
from .downloader import FFmpegAudioExtractor, HttpPanoptoDownloader
from .lectures_service import LecturesService
from .schemas import (
    DocumentDetailResponse,
    DocumentUploadResponse,
    LectureDetailResponse,
    LectureDownloadRequest,
    LectureDownloadResponse,
    LectureStatusResponse,
)
from .storage import LocalStorageBackend

app = FastAPI(title="StudyBuddy Backend")

storage_backend = LocalStorageBackend(settings.storage_root)
lectures_service = LecturesService(
    storage=storage_backend,
    downloader=HttpPanoptoDownloader(),
    extractor=FFmpegAudioExtractor(),
)
documents_service = DocumentsService(storage_backend)


@app.post("/api/lectures/download", response_model=LectureDownloadResponse)
async def trigger_lecture_download(
    payload: LectureDownloadRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    try:
        lecture, created = lectures_service.request_download(
            db,
            payload,
            user_id=current_user.user_id,
            background_tasks=background_tasks,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    response = LectureDownloadResponse(lecture_id=lecture.id, status=lecture.status)
    status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=status_code, content=response.model_dump())


@app.get("/api/lectures/{lecture_id}", response_model=LectureDetailResponse)
async def get_lecture(
    lecture_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    try:
        lecture = lectures_service.fetch_lecture_for_user(db, lecture_id, current_user.user_id)
    except NoResultFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    return LectureDetailResponse(
        id=lecture.id,
        course_id=lecture.course_id,
        panopto_session_id=lecture.panopto_session_id,
        panopto_url=lecture.panopto_url,
        title=lecture.title,
        duration_seconds=lecture.duration_seconds,
        status=lecture.status,
        error_message=lecture.error_message,
        created_at=lecture.created_at,
        updated_at=lecture.updated_at,
    )


@app.get("/api/lectures/{lecture_id}/status", response_model=LectureStatusResponse)
async def get_lecture_status(
    lecture_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    try:
        lecture = lectures_service.fetch_lecture_for_user(db, lecture_id, current_user.user_id)
    except NoResultFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")

    return LectureStatusResponse(
        lecture_id=lecture.id,
        status=lecture.status,
        error_message=lecture.error_message,
        duration_seconds=lecture.duration_seconds,
    )


@app.post("/api/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    course_id: UUID = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    filename = file.filename or ""
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF uploads are supported")
    if file.content_type not in {"application/pdf", "application/x-pdf", "application/octet-stream"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid PDF content type")

    file_bytes = await file.read()
    document, created = documents_service.upload_document(
        db,
        course_id=course_id,
        user_id=current_user.user_id,
        filename=file.filename,
        content_type=file.content_type,
        file_bytes=file_bytes,
    )
    response = DocumentUploadResponse(document_id=document.id, course_id=document.course_id, status=document.status)
    status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return JSONResponse(status_code=status_code, content=response.model_dump())


@app.get("/api/documents/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    try:
        document = documents_service.fetch_document_for_user(db, document_id, current_user.user_id)
    except NoResultFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    return DocumentDetailResponse(
        id=document.id,
        course_id=document.course_id,
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        page_count=document.page_count,
        status=document.status,
        created_at=document.created_at,
        updated_at=document.updated_at,
    )


@app.get("/api/documents/{document_id}/file")
async def download_document_file(
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    try:
        document = documents_service.fetch_document_for_user(db, document_id, current_user.user_id)
    except NoResultFound:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")

    try:
        file_stream = storage_backend.open_file(document.storage_key)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored file not found") from exc
    headers = {
        "Content-Disposition": f'attachment; filename="{document.filename}"'
    }
    return StreamingResponse(file_stream, media_type=document.mime_type, headers=headers)
