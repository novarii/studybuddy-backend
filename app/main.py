from __future__ import annotations

import logging
from uuid import UUID

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from agno.os import AgentOS

from .api.auth import AuthenticatedUser, require_user
from .agents.chat_agent import create_chat_agent
from .core.config import settings
from .database.db import SessionLocal, get_db
from .database.models import Course, Lecture
from .schemas import (
    CourseResponse,
    DocumentDetailResponse,
    DocumentUploadResponse,
    LectureDetailResponse,
    LectureDownloadRequest,
    LectureDownloadResponse,
    LectureStatusListItem,
    LectureStatusResponse,
)
from .services.document_chunk_pipeline import DocumentChunkPipeline, DocumentChunkPipelineError
from .services.documents_service import DocumentsService
from .services.downloaders.downloader import FFmpegAudioExtractor
from .services.downloaders.panopto_downloader import PanoptoPackageDownloader
from .services.lecture_chunk_pipeline import LectureChunkPipeline
from .services.lectures_service import LecturesService
from .services.transcription_service import WhisperTranscriptionClient
from .storage import LocalStorageBackend

app = FastAPI(title="StudyBuddy Backend")

if not settings.cors_allow_origins:
    raise RuntimeError("CORS_ALLOW_ORIGINS must be configured")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allow_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

storage_backend = LocalStorageBackend(settings.storage_root)
lecture_chunk_pipeline = LectureChunkPipeline(storage_backend)
whisper_client = None
if settings.whisper_server_ip:
    whisper_port = settings.whisper_server_port or 80
    whisper_base_url = f"http://{settings.whisper_server_ip}:{whisper_port}"
    whisper_client = WhisperTranscriptionClient(
        base_url=whisper_base_url,
        request_timeout=settings.whisper_request_timeout_seconds,
        poll_interval=settings.whisper_poll_interval_seconds,
        poll_timeout=settings.whisper_poll_timeout_seconds,
    )
lectures_service = LecturesService(
    storage=storage_backend,
    downloader=PanoptoPackageDownloader(),
    extractor=FFmpegAudioExtractor(),
    transcriber=whisper_client,
    lecture_chunk_pipeline=lecture_chunk_pipeline,
)
documents_service = DocumentsService(storage_backend)
document_chunk_pipeline = DocumentChunkPipeline(storage_backend)
chat_agent = create_chat_agent()

logger = logging.getLogger(__name__)


def _process_document_pipeline(
    document_id: UUID,
    storage_key: str,
    owner_id: UUID,
    course_id: UUID,
) -> None:
    try:
        document_chunk_pipeline.process_document(
            document_id,
            storage_key,
            owner_id,
            course_id,
        )
    except DocumentChunkPipelineError:
        logger.exception("Document chunk pipeline failed for %s", document_id)
        document_chunk_pipeline.cleanup_document(document_id)
        db = SessionLocal()
        try:
            documents_service.delete_document(db, document_id)
        except Exception:
            logger.exception("Failed to delete document %s after chunk failure", document_id)
        finally:
            db.close()

@app.get("/api/health")
async def health_check():
    return {"status": "ok"}


@app.get("/api/dev/lectures", response_model=list[LectureStatusListItem])
async def list_dev_lectures(db: Session = Depends(get_db)):
    if not settings.dev_routes_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    lectures = (
        db.execute(select(Lecture).order_by(Lecture.created_at.desc()))
        .scalars()
        .all()
    )
    return [
        LectureStatusListItem(
            id=lecture.id,
            title=lecture.title,
            status=lecture.status,
            created_at=lecture.created_at,
            updated_at=lecture.updated_at,
        )
        for lecture in lectures
    ]


@app.get("/api/courses", response_model=list[CourseResponse])
async def list_courses(
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    courses = db.execute(select(Course).order_by(Course.code)).scalars().all()
    return [
        CourseResponse(
            id=course.id,
            code=course.code,
            title=course.title,
            instructor=course.instructor,
        )
        for course in courses
    ]


@app.post("/api/lectures/download", response_model=LectureDownloadResponse)
async def trigger_lecture_download(
    payload: LectureDownloadRequest,
    background_tasks: BackgroundTasks,
    response: Response,
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

    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return LectureDownloadResponse(lecture_id=lecture.id, status=lecture.status)


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
        stream_url=lecture.stream_url,
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


@app.delete("/api/lectures/{lecture_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lecture(
    lecture_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    if current_user.user_id in settings.admin_user_ids:
        try:
            lectures_service.delete_lecture(db, lecture_id)
        except NoResultFound:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lecture not found")
    else:
        lectures_service.fetch_lecture_for_user(db, lecture_id, current_user.user_id)
        lectures_service.remove_user_from_lecture(db, lecture_id, current_user.user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/documents/upload", response_model=DocumentUploadResponse)
async def upload_document(
    response: Response,
    background_tasks: BackgroundTasks,
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
    if created:
        background_tasks.add_task(
            _process_document_pipeline,
            document.id,
            document.storage_key,
            document.owner_id,
            document.course_id,
        )
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return DocumentUploadResponse(
        document_id=document.id,
        course_id=document.course_id,
        status=document.status,
    )


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


@app.delete("/api/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    if current_user.user_id in settings.admin_user_ids:
        try:
            documents_service.delete_document(db, document_id)
        except NoResultFound:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    else:
        documents_service.fetch_document_for_user(db, document_id, current_user.user_id)
        documents_service.remove_user_from_document(db, document_id, current_user.user_id)
    document_chunk_pipeline.cleanup_document(document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


agent_os = AgentOS(
    description="StudyBuddy AgentOS",
    agents=[chat_agent],
    base_app=app,
)
app = agent_os.get_app()
