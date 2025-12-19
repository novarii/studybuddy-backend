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

from .adapters import AgnoVercelAdapter, get_vercel_stream_headers
from .api.auth import AuthenticatedUser, require_user
from .agents.chat_agent import create_chat_agent, create_test_chat_agent, retrieve_documents
from .agents.context_formatter import format_retrieval_context
from .core.config import settings
from .database.db import SessionLocal, get_db
from .database.models import Course, Lecture, UserCourse
from .schemas import (
    ChatRequest,
    CourseResponse,
    CourseSyncResponse,
    DocumentDetailResponse,
    DocumentUploadResponse,
    LectureDetailResponse,
    LectureDownloadRequest,
    LectureDownloadResponse,
    LectureStatusListItem,
    LectureStatusResponse,
)
from .services.course_sync_service import CourseSyncService
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
course_sync_service = CourseSyncService()

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


@app.get("/api/user/courses", response_model=list[CourseResponse])
async def list_user_courses(
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Get all courses the current user has added."""
    user_courses = (
        db.execute(
            select(Course)
            .join(UserCourse, UserCourse.course_id == Course.id)
            .where(UserCourse.user_id == current_user.user_id)
            .order_by(Course.code)
        )
        .scalars()
        .all()
    )
    return [
        CourseResponse(
            id=course.id,
            code=course.code,
            title=course.title,
            instructor=course.instructor,
        )
        for course in user_courses
    ]


@app.post("/api/user/courses/{course_id}", status_code=status.HTTP_201_CREATED)
async def add_user_course(
    course_id: UUID,
    response: Response,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Add a course to the current user's list."""
    # Verify course exists
    course = db.execute(select(Course).where(Course.id == course_id)).scalar_one_or_none()
    if not course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    # Check if already added
    existing = db.execute(
        select(UserCourse).where(
            UserCourse.user_id == current_user.user_id,
            UserCourse.course_id == course_id,
        )
    ).scalar_one_or_none()

    if existing:
        response.status_code = status.HTTP_200_OK
        return {"message": "Course already added"}

    user_course = UserCourse(user_id=current_user.user_id, course_id=course_id)
    db.add(user_course)
    db.commit()
    return {"message": "Course added"}


@app.delete("/api/user/courses/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_user_course(
    course_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Remove a course from the current user's list."""
    user_course = db.execute(
        select(UserCourse).where(
            UserCourse.user_id == current_user.user_id,
            UserCourse.course_id == course_id,
        )
    ).scalar_one_or_none()

    if not user_course:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not in user's list")

    db.delete(user_course)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/admin/courses/sync", response_model=CourseSyncResponse)
async def sync_courses_from_catalog(
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    """
    Sync courses from CDCS catalog (admin only).

    Fetches lecture-type courses from University of Rochester CDCS
    for Fall 2025 and Spring 2025, deduplicates by course code,
    and upserts into the courses table with is_official=true.
    """
    if current_user.user_id not in settings.admin_user_ids:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )

    result = course_sync_service.sync_courses(db)
    return CourseSyncResponse(
        created=result.created,
        updated=result.updated,
        unchanged=result.unchanged,
        deleted=result.deleted,
        total=result.total,
        terms=result.terms,
        deletion_skipped=result.deletion_skipped,
    )


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


@app.post("/api/agent/chat")
async def chat_stream(
    payload: ChatRequest,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """
    Streaming chat endpoint compatible with Vercel AI SDK v5.

    Returns Server-Sent Events in the Vercel UI Message Stream format.
    Sources are emitted BEFORE text begins for "searching..." UI states.

    The response includes:
    - RAG sources with full metadata (document_id, lecture_id, slide_number, timestamps)
      plus chunk_number for correlating citations like [1], [2] in the response
    - Streaming text deltas
    - Reasoning steps (if model provides them)
    - Tool calls (if agent uses tools)

    The model receives a lean, numbered context (no metadata bloat), while the
    client receives rich metadata for UI rendering.
    """

    async def generate_stream():
        adapter = AgnoVercelAdapter()

        try:
            # 1. Retrieve raw references
            raw_references = []
            try:
                raw_references = retrieve_documents(
                    query=payload.message,
                    owner_id=current_user.user_id,
                    course_id=payload.course_id,
                    document_id=payload.document_id,
                    lecture_id=payload.lecture_id,
                )
            except Exception as e:
                logger.warning("Pre-retrieval failed: %s", e)

            # 2. Format into lean model context + rich client sources
            formatted = format_retrieval_context(raw_references, order_by="chronological")

            # 3. Extract sources with chunk_number for client
            pre_sources = adapter.extract_sources_from_references(formatted.client_sources)

            # 4. Build user message with lean context injected
            if formatted.model_context:
                user_message = (
                    f"{payload.message}\n\n"
                    f"<references>\n{formatted.model_context}\n</references>"
                )
            else:
                user_message = payload.message

            # 5. Create agent and run with streaming
            agent = create_chat_agent()
            stream = agent.run(
                input=user_message,
                stream=True,
                stream_events=True,
                user_id=str(current_user.user_id),
            )

            # 6. Transform Agno stream to Vercel format
            for chunk in adapter.transform_stream_sync(
                stream,
                pre_retrieved_sources=pre_sources,
            ):
                yield chunk

        except Exception as e:
            logger.exception("Chat stream error")
            yield adapter._emit_error(f"Stream error: {str(e)}")
            yield adapter._emit_done()

    return StreamingResponse(
        generate_stream(),
        headers=get_vercel_stream_headers(),
    )


test_agent = create_test_chat_agent()
agent_os = AgentOS(
    description="StudyBuddy AgentOS",
    agents=[test_agent],
    base_app=app,
)
app = agent_os.get_app()
