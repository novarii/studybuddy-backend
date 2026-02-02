from __future__ import annotations

import json
import logging
import re
import uuid as uuid_module
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote
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
from .agents.chat_agent import create_chat_agent, create_search_tool, create_test_chat_agent, get_agno_db
from .core.config import settings
from .database.db import SessionLocal, get_db
from .database.models import Course, Lecture, UserCourse
from .schemas import (
    ChatRequest,
    CourseResponse,
    CourseSyncResponse,
    CreateSessionRequest,
    CreateSessionResponse,
    DocumentDetailResponse,
    DocumentUploadResponse,
    GenerateTitleResponse,
    LectureAudioUploadMetadata,
    LectureAudioUploadResponse,
    LectureDetailResponse,
    LectureDownloadRequest,
    LectureDownloadResponse,
    LectureStatusListItem,
    LectureStatusResponse,
    MessageResponse,
    RAGSourceResponse,
    SessionListResponse,
    SessionResponse,
)
from .services.course_sync_service import CourseSyncService
from .services.document_chunk_pipeline import DocumentChunkPipeline, DocumentChunkPipelineError
from .services.documents_service import DocumentsService
from .services.downloaders.downloader import FFmpegAudioExtractor
from .services.downloaders.panopto_downloader import PanoptoPackageDownloader
from .services.lecture_chunk_pipeline import LectureChunkPipeline
from .services.lectures_service import LecturesService
from .services.transcription_service import WhisperTranscriptionClient
from .services.message_sources_service import save_message_sources, load_sources_for_messages, delete_sources_for_session
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
async def list_dev_lectures(
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    # Defense in depth: require both dev routes enabled AND admin user
    if not settings.dev_routes_enabled or current_user.user_id not in settings.admin_user_ids:
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
    """
    Fallback endpoint for lecture download via CloudFront HLS URL.

    Used when the browser extension cannot fetch audio directly from Panopto's
    audioPodcast endpoint (e.g., encoding not complete, auth issues).
    """
    logger.info("Lecture upload via CloudFront (fallback)")
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


@app.post("/api/lectures/audio", response_model=LectureAudioUploadResponse)
async def upload_lecture_audio(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    metadata: str = Form(...),
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    """
    Primary endpoint for direct audio upload from browser extension.

    This is the preferred path when the extension can fetch audio directly
    from Panopto's audioPodcast endpoint (~75MB vs ~500MB video download).

    Args:
        audio: M4A/MP4 audio file from Panopto audioPodcast endpoint
        metadata: JSON string with session_id, course_id, title, duration
    """
    # Parse and validate metadata
    try:
        meta_dict = json.loads(metadata)
        meta = LectureAudioUploadMetadata(**meta_dict)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid metadata: {exc}",
        ) from exc

    # Validate audio file
    if not audio.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Audio file must have a filename",
        )

    content_type = audio.content_type or ""
    if not content_type.startswith(("audio/", "video/mp4", "application/octet-stream")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid audio content type: {content_type}",
        )

    # Enforce 100MB file size limit for audio
    max_file_size = 100 * 1024 * 1024  # 100 MB
    if audio.size is not None and audio.size > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio file too large. Maximum size is {max_file_size // (1024 * 1024)} MB",
        )

    # Read audio file with size limit
    audio_bytes = await audio.read(max_file_size + 1)
    if len(audio_bytes) > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Audio file too large. Maximum size is {max_file_size // (1024 * 1024)} MB",
        )

    lecture, created = lectures_service.upload_audio(
        db,
        metadata=meta,
        audio_bytes=audio_bytes,
        user_id=current_user.user_id,
        background_tasks=background_tasks,
    )

    return LectureAudioUploadResponse(
        lecture_id=lecture.id,
        status=lecture.status,
        created=created,
    )


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


@app.get("/api/courses/{course_id}/documents", response_model=list[DocumentDetailResponse])
async def list_course_documents(
    course_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    """List all documents for a course owned by the current user."""
    documents = documents_service.list_documents_for_course(db, course_id, current_user.user_id)
    return [
        DocumentDetailResponse(
            id=doc.id,
            course_id=doc.course_id,
            filename=doc.filename,
            mime_type=doc.mime_type,
            size_bytes=doc.size_bytes,
            page_count=doc.page_count,
            status=doc.status,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
        )
        for doc in documents
    ]


@app.get("/api/courses/{course_id}/lectures", response_model=list[LectureDetailResponse])
async def list_course_lectures(
    course_id: UUID,
    db: Session = Depends(get_db),
    current_user: AuthenticatedUser = Depends(require_user),
):
    """List all lectures for a course that the user has access to."""
    lectures = lectures_service.list_lectures_for_course(db, course_id, current_user.user_id)
    return [
        LectureDetailResponse(
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
        for lecture in lectures
    ]


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

    # Enforce 50MB file size limit
    max_file_size = 50 * 1024 * 1024  # 50 MB
    if file.size is not None and file.size > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {max_file_size // (1024 * 1024)} MB",
        )

    # Read file with size limit to prevent memory exhaustion
    file_bytes = await file.read(max_file_size + 1)
    if len(file_bytes) > max_file_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large. Maximum size is {max_file_size // (1024 * 1024)} MB",
        )
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
    # Use RFC 5987 encoding to prevent header injection from malicious filenames
    # Use "inline" to display in iframe/browser, not force download
    safe_filename = quote(document.filename, safe='')
    headers = {
        "Content-Disposition": f"inline; filename*=UTF-8''{safe_filename}"
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

    The agent has access to a search_course_materials tool that it can use
    to search the student's lecture transcripts and slide decks when needed.
    The agent decides when to use the tool based on the user's question.
    """

    async def generate_stream():
        adapter = AgnoVercelAdapter()
        agent = None

        try:
            # Create search tool with user's context
            search_tool = create_search_tool(
                owner_id=current_user.user_id,
                course_id=payload.course_id,
                document_id=payload.document_id,
                lecture_id=payload.lecture_id,
            )

            # Create agent with persistence and search tool
            agno_db = get_agno_db()
            agent = create_chat_agent(db=agno_db, tools=[search_tool])

            stream = agent.run(
                input=payload.message,
                stream=True,
                stream_events=True,
                session_id=payload.session_id,
                user_id=str(current_user.user_id),
                session_state={"course_id": str(payload.course_id)},
            )

            # Transform Agno stream to Vercel format
            for chunk in adapter.transform_stream_sync(stream):
                yield chunk

        except Exception as e:
            logger.exception("Chat stream error")
            yield adapter._emit_error(f"Stream error: {str(e)}")
            yield adapter._emit_done()
        finally:
            if not payload.session_id or not agent:
                return

            session = None
            # Persist collected sources after streaming completes
            if adapter.collected_sources and adapter.agno_run_id:
                try:
                    # Get the actual assistant message ID from Agno's run
                    session = agent.get_session(session_id=payload.session_id)
                    if session:
                        run = session.get_run(adapter.agno_run_id)
                        if run and run.messages:
                            # Find the assistant message in this run
                            for msg in reversed(run.messages):
                                if msg.role == "assistant" and msg.id:
                                    with SessionLocal() as db_session:
                                        save_message_sources(
                                            db_session,
                                            message_id=msg.id,
                                            session_id=payload.session_id,
                                            sources=adapter.collected_sources,
                                        )
                                    break
                except Exception as e:
                    logger.warning(f"Failed to save message sources: {e}")

            # Auto-generate session title after first message
            try:
                if session is None:
                    session = agent.get_session(session_id=payload.session_id)
                if session:
                    session_data = session.session_data or {}
                    if not session_data.get("session_name"):
                        agent.set_session_name(session_id=payload.session_id, autogenerate=True)
                        # Clean up generated title (remove "StudyBuddy" mentions)
                        session = agent.get_session(session_id=payload.session_id)
                        if session:
                            generated_name = (session.session_data or {}).get("session_name", "")
                            if generated_name:
                                cleaned = re.sub(r'\bstudybuddy\b', '', generated_name, flags=re.IGNORECASE).strip()
                                cleaned = re.sub(r'\s+', ' ', cleaned).strip(" -:,")  # Clean up extra spaces/punctuation
                                if cleaned and cleaned != generated_name:
                                    agent.set_session_name(session_id=payload.session_id, name=cleaned)
            except Exception as e:
                logger.warning(f"Failed to auto-generate session title: {e}")

    return StreamingResponse(
        generate_stream(),
        headers=get_vercel_stream_headers(),
    )


# --- Session Management Endpoints ---


@app.get("/api/sessions", response_model=SessionListResponse)
async def list_sessions(
    course_id: Optional[UUID] = None,
    page: int = 1,
    limit: int = 20,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """List user's chat sessions, optionally filtered by course."""
    agno_db = get_agno_db()
    agent = create_chat_agent(db=agno_db)

    # Query sessions from Agno's session table
    # We need to query the database directly since Agno doesn't provide a list_sessions method
    from sqlalchemy import text

    offset = (page - 1) * limit
    user_id_str = str(current_user.user_id)

    # Query the agno_sessions table directly
    with agno_db.Session() as db_session:
        # Build query with optional course_id filter
        # course_id is stored in session_data->'session_state'->>'course_id'
        if course_id:
            query = text("""
                SELECT session_id, session_data, metadata, created_at, updated_at
                FROM ai.agno_sessions
                WHERE user_id = :user_id
                AND session_data->'session_state'->>'course_id' = :course_id
                ORDER BY updated_at DESC
                LIMIT :limit OFFSET :offset
            """)
            count_query = text("""
                SELECT COUNT(*) FROM ai.agno_sessions
                WHERE user_id = :user_id
                AND session_data->'session_state'->>'course_id' = :course_id
            """)
            params = {"user_id": user_id_str, "course_id": str(course_id), "limit": limit, "offset": offset}
        else:
            query = text("""
                SELECT session_id, session_data, metadata, created_at, updated_at
                FROM ai.agno_sessions
                WHERE user_id = :user_id
                ORDER BY updated_at DESC
                LIMIT :limit OFFSET :offset
            """)
            count_query = text("""
                SELECT COUNT(*) FROM ai.agno_sessions
                WHERE user_id = :user_id
            """)
            params = {"user_id": user_id_str, "limit": limit, "offset": offset}

        result = db_session.execute(query, params)
        rows = result.fetchall()

        count_result = db_session.execute(
            count_query,
            {"user_id": user_id_str, "course_id": str(course_id)} if course_id else {"user_id": user_id_str}
        )
        total = count_result.scalar() or 0

    sessions = []
    for row in rows:
        session_data = row.session_data or {}
        session_state = session_data.get("session_state", {})
        sessions.append(SessionResponse(
            session_id=row.session_id,
            session_name=session_data.get("session_name"),
            course_id=session_state.get("course_id"),
            created_at=datetime.fromtimestamp(row.created_at) if row.created_at else datetime.now(),
            updated_at=datetime.fromtimestamp(row.updated_at) if row.updated_at else datetime.now(),
        ))

    return SessionListResponse(
        sessions=sessions,
        total=total,
        page=page,
        limit=limit,
    )


@app.post("/api/sessions", response_model=CreateSessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: CreateSessionRequest,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Create a new chat session for a course."""
    session_id = str(uuid_module.uuid4())

    # The session will be created in Agno on first message
    # We just return the session_id for now
    return CreateSessionResponse(session_id=session_id)


@app.get("/api/sessions/{session_id}/messages", response_model=List[MessageResponse])
async def get_session_messages(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Get all messages in a session."""
    agno_db = get_agno_db()
    agent = create_chat_agent(db=agno_db)

    # Verify session belongs to user
    session = agent.get_session(session_id=session_id)
    if not session or session.user_id != str(current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Get chat history (user + assistant messages only)
    messages = agent.get_chat_history(session_id=session_id)
    filtered_messages = [msg for msg in messages if msg.role in ("user", "assistant")]

    # Load sources for assistant messages
    assistant_msg_ids = [
        msg.id for msg in filtered_messages
        if msg.role == "assistant" and msg.id
    ]
    sources_by_message = load_sources_for_messages(db, assistant_msg_ids) if assistant_msg_ids else {}

    return [
        MessageResponse(
            id=msg.id or str(uuid_module.uuid4()),
            role=msg.role,
            content=msg.content or "",
            created_at=datetime.fromtimestamp(msg.created_at) if hasattr(msg, 'created_at') and msg.created_at else None,
            sources=[
                RAGSourceResponse(**src) for src in sources_by_message.get(msg.id, [])
            ] if msg.role == "assistant" and msg.id else None,
        )
        for msg in filtered_messages
    ]


@app.delete("/api/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Delete a chat session and all its messages."""
    agno_db = get_agno_db()
    agent = create_chat_agent(db=agno_db)

    # Verify ownership
    session = agent.get_session(session_id=session_id)
    if not session or session.user_id != str(current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Delete message sources first, then the session
    delete_sources_for_session(db, session_id)
    agent.delete_session(session_id=session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@app.post("/api/sessions/{session_id}/generate-title", response_model=GenerateTitleResponse)
async def generate_session_title(
    session_id: str,
    current_user: AuthenticatedUser = Depends(require_user),
):
    """Auto-generate a title for the session based on conversation content."""
    agno_db = get_agno_db()
    agent = create_chat_agent(db=agno_db)

    # Verify ownership
    session = agent.get_session(session_id=session_id)
    if not session or session.user_id != str(current_user.user_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # Get chat history to generate title from
    messages = agent.get_chat_history(session_id=session_id)
    if not messages:
        return GenerateTitleResponse(session_name=None)

    # Generate title from first user message (simple approach)
    first_user_msg = next((m for m in messages if m.role == "user"), None)
    if first_user_msg and first_user_msg.content:
        # Truncate to first 50 chars as a simple title
        title = first_user_msg.content[:50]
        if len(first_user_msg.content) > 50:
            title += "..."

        # Update session name in database
        from sqlalchemy import text
        with agno_db.Session() as db_session:
            db_session.execute(
                text("""
                    UPDATE ai.agno_sessions
                    SET session_data = jsonb_set(
                        COALESCE(session_data, '{}'::jsonb),
                        '{session_name}',
                        :title::jsonb
                    )
                    WHERE session_id = :session_id
                """),
                {"session_id": session_id, "title": json.dumps(title)}
            )
            db_session.commit()

        return GenerateTitleResponse(session_name=title)

    return GenerateTitleResponse(session_name=None)


test_agent = create_test_chat_agent()
agent_os = AgentOS(
    description="StudyBuddy AgentOS",
    agents=[test_agent],
    base_app=app,
)
app = agent_os.get_app()
