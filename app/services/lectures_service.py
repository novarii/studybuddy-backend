from __future__ import annotations

import io
import json
import logging
from typing import Optional
from uuid import UUID, uuid4

from fastapi import BackgroundTasks
from sqlalchemy import and_, select
from sqlalchemy.exc import NoResultFound
from sqlalchemy.orm import Session

from ..core.utils import extract_panopto_session_id
from ..database.db import SessionLocal
from ..database.models import Lecture, LectureStatus, UserLecture
from ..schemas import LectureAudioUploadMetadata, LectureDownloadRequest
from ..storage import StorageBackend
from .downloaders.downloader import (
    AudioExtractionError,
    AudioExtractor,
    DownloadError,
    PanoptoDownloader,
)
from .lecture_chunk_pipeline import LectureChunkPipeline
from .transcription_service import TranscriptionError, WhisperTranscriptionClient
from .users_service import ensure_user_exists

logger = logging.getLogger(__name__)


class LecturesService:
    """Encapsulates lecture persistence and download workflows."""

    def __init__(
        self,
        storage: StorageBackend,
        downloader: PanoptoDownloader,
        extractor: AudioExtractor,
        transcriber: Optional[WhisperTranscriptionClient] = None,
        lecture_chunk_pipeline: Optional[LectureChunkPipeline] = None,
    ) -> None:
        self.storage = storage
        self.downloader = downloader
        self.extractor = extractor
        self.transcriber = transcriber
        self.lecture_chunk_pipeline = lecture_chunk_pipeline

    def request_download(
        self,
        db: Session,
        payload: LectureDownloadRequest,
        user_id: UUID,
        background_tasks: Optional[BackgroundTasks] = None,
    ) -> tuple[Lecture, bool]:
        session_id = extract_panopto_session_id(payload.panopto_url)
        lecture = (
            db.execute(
                select(Lecture).where(
                    and_(
                        Lecture.course_id == payload.course_id,
                        Lecture.panopto_session_id == session_id,
                    )
                )
            )
            .scalars()
            .first()
        )

        created = False
        if lecture is None:
            lecture = Lecture(
                id=uuid4(),
                course_id=payload.course_id,
                panopto_session_id=session_id,
                panopto_url=payload.panopto_url,
                stream_url=payload.stream_url,
                title=payload.title,
                status=LectureStatus.pending,
            )
            db.add(lecture)
            created = True
        else:
            lecture.panopto_url = payload.panopto_url
            lecture.stream_url = payload.stream_url
            lecture.title = payload.title or lecture.title

        ensure_user_exists(db, user_id)
        self._ensure_user_link(db, user_id, lecture.id)
        db.commit()

        if created and background_tasks is not None:
            background_tasks.add_task(self._run_download_pipeline, lecture.id)

        return lecture, created

    def upload_audio(
        self,
        db: Session,
        metadata: LectureAudioUploadMetadata,
        audio_bytes: bytes,
        user_id: UUID,
        background_tasks: Optional[BackgroundTasks] = None,
    ) -> tuple[Lecture, bool]:
        """
        Handle direct audio upload from browser extension.

        This is the primary/preferred path when the extension can fetch audio
        directly from Panopto's audioPodcast endpoint.

        Returns (lecture, created) tuple.
        """
        logger.info("Lecture upload via audioPodcast (primary)")

        # Check for existing lecture by course + session ID
        lecture = (
            db.execute(
                select(Lecture).where(
                    and_(
                        Lecture.course_id == metadata.course_id,
                        Lecture.panopto_session_id == metadata.session_id,
                    )
                )
            )
            .scalars()
            .first()
        )

        created = False
        if lecture is None:
            # Construct panopto_url from session ID
            panopto_url = (
                f"https://rochester.hosted.panopto.com/Panopto/Pages/Viewer.aspx"
                f"?id={metadata.session_id}"
            )

            lecture = Lecture(
                id=uuid4(),
                course_id=metadata.course_id,
                panopto_session_id=metadata.session_id,
                panopto_url=panopto_url,
                stream_url="audio_podcast",  # Marker indicating direct audio upload
                title=metadata.title,
                status=LectureStatus.pending,
                duration_seconds=int(metadata.duration) if metadata.duration else None,
            )
            db.add(lecture)
            created = True

            # Save audio file to storage
            audio_storage_key = f"audio/{lecture.id}.m4a"
            audio_stream = io.BytesIO(audio_bytes)
            self.storage.store_file(
                audio_storage_key,
                audio_stream,
                mime_type="audio/mp4",
            )
            lecture.audio_storage_key = audio_storage_key
        else:
            # Update title if provided
            if metadata.title:
                lecture.title = metadata.title

        ensure_user_exists(db, user_id)
        self._ensure_user_link(db, user_id, lecture.id)
        db.commit()

        if created and background_tasks is not None:
            background_tasks.add_task(self._run_audio_pipeline, lecture.id)

        return lecture, created

    def _run_audio_pipeline(self, lecture_id: UUID) -> None:
        """
        Run transcription pipeline for directly uploaded audio.

        This is a simpler pipeline than _run_download_pipeline since we already
        have the audio file - we just need to transcribe it.
        """
        db = SessionLocal()
        temp_keys: list[str] = []
        try:
            lecture = db.get(Lecture, lecture_id)
            if lecture is None:
                return

            lecture.status = LectureStatus.downloading  # Reuse status for consistency
            lecture.error_message = None
            db.commit()

            if self.transcriber is not None and lecture.audio_storage_key:
                try:
                    transcription_result = self.transcriber.transcribe(
                        self.storage, lecture.audio_storage_key
                    )
                except TranscriptionError as exc:
                    logger.warning("Transcription skipped for lecture %s: %s", lecture_id, exc)
                else:
                    transcript_storage_key = f"transcripts/{lecture.id}.json"
                    transcript_stream = io.BytesIO(
                        json.dumps(transcription_result.raw_payload).encode("utf-8")
                    )
                    self.storage.store_file(
                        transcript_storage_key,
                        transcript_stream,
                        mime_type="application/json",
                    )
                    temp_keys.append(transcript_storage_key)
                    lecture.transcript_storage_key = transcript_storage_key

                    # Update duration from transcription if not already set
                    if (
                        lecture.duration_seconds is None
                        and transcription_result.raw_payload.get("duration")
                    ):
                        lecture.duration_seconds = int(
                            transcription_result.raw_payload["duration"]
                        )

                    if transcription_result.vtt_content:
                        vtt_storage_key = f"transcripts/{lecture.id}.vtt"
                        vtt_stream = io.BytesIO(
                            transcription_result.vtt_content.encode("utf-8")
                        )
                        self.storage.store_file(
                            vtt_storage_key,
                            vtt_stream,
                            mime_type="text/vtt",
                        )
                        temp_keys.append(vtt_storage_key)

                    if self.lecture_chunk_pipeline is not None:
                        try:
                            self.lecture_chunk_pipeline.process_transcript_segments(
                                lecture_id=lecture.id,
                                course_id=lecture.course_id,
                                segments=transcription_result.segments,
                            )
                        except Exception:  # pragma: no cover - background safety
                            logger.exception(
                                "Lecture chunk pipeline failed for lecture %s", lecture_id
                            )

            lecture.status = LectureStatus.completed
            lecture.error_message = None
            db.commit()

        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Audio pipeline failed for lecture %s", lecture_id)
            self._handle_pipeline_failure(db, lecture_id, str(exc), temp_keys=temp_keys)
        finally:
            db.close()

    def list_lectures_for_course(
        self, db: Session, course_id: UUID, user_id: UUID
    ) -> list[Lecture]:
        """List all lectures for a course that the user has access to."""
        stmt = (
            select(Lecture)
            .join(UserLecture, UserLecture.lecture_id == Lecture.id)
            .where(
                UserLecture.user_id == user_id,
                Lecture.course_id == course_id,
            )
            .order_by(Lecture.created_at.desc())
        )
        return list(db.execute(stmt).scalars().all())

    def fetch_lecture_for_user(self, db: Session, lecture_id: UUID, user_id: UUID) -> Lecture:
        stmt = (
            select(Lecture)
            .join(UserLecture, UserLecture.lecture_id == Lecture.id)
            .where(UserLecture.user_id == user_id, Lecture.id == lecture_id)
        )
        lecture = db.execute(stmt).scalars().first()
        if lecture is None:
            raise NoResultFound("Lecture not found for user")
        return lecture

    def remove_user_from_lecture(self, db: Session, lecture_id: UUID, user_id: UUID) -> None:
        link = (
            db.execute(
                select(UserLecture).where(
                    UserLecture.user_id == user_id, UserLecture.lecture_id == lecture_id
                )
            )
            .scalars()
            .first()
        )
        if link is None:
            return
        db.delete(link)
        db.flush()

        remaining = (
            db.execute(
                select(UserLecture).where(UserLecture.lecture_id == lecture_id).limit(1)
            )
            .scalars()
            .first()
        )
        if remaining is None:
            lecture = db.get(Lecture, lecture_id)
            if lecture:
                self._cleanup_lecture_assets(lecture)
                db.delete(lecture)
        db.commit()

    def delete_lecture(self, db: Session, lecture_id: UUID) -> None:
        lecture = db.get(Lecture, lecture_id)
        if lecture is None:
            raise NoResultFound("Lecture not found")
        self._cleanup_lecture_assets(lecture)
        db.delete(lecture)
        db.commit()

    def _run_download_pipeline(self, lecture_id: UUID) -> None:
        db = SessionLocal()
        temp_keys: list[str] = []
        try:
            lecture = db.get(Lecture, lecture_id)
            if lecture is None:
                return

            lecture.status = LectureStatus.downloading
            lecture.error_message = None
            db.commit()

            video_storage_key = f"audio_tmp/{lecture.id}_source.mp4"
            download_result = self.downloader.download_video(
                lecture.stream_url,
                self.storage,
                video_storage_key,
            )
            temp_keys.append(download_result.storage_key)
            audio_storage_key = f"audio/{lecture.id}.m4a"
            audio_result = self.extractor.extract_audio(
                self.storage, download_result.storage_key, audio_storage_key
            )
            temp_keys.append(audio_result.storage_key)
            lecture.audio_storage_key = audio_result.storage_key
            lecture.duration_seconds = audio_result.duration_seconds

            if self.transcriber is not None:
                try:
                    transcription_result = self.transcriber.transcribe(self.storage, audio_result.storage_key)
                except TranscriptionError as exc:
                    logger.warning("Transcription skipped for lecture %s: %s", lecture_id, exc)
                else:
                    transcript_storage_key = f"transcripts/{lecture.id}.json"
                    transcript_stream = io.BytesIO(
                        json.dumps(transcription_result.raw_payload).encode("utf-8")
                    )
                    self.storage.store_file(
                        transcript_storage_key,
                        transcript_stream,
                        mime_type="application/json",
                    )
                    temp_keys.append(transcript_storage_key)
                    lecture.transcript_storage_key = transcript_storage_key

                    if transcription_result.vtt_content:
                        vtt_storage_key = f"transcripts/{lecture.id}.vtt"
                        vtt_stream = io.BytesIO(transcription_result.vtt_content.encode("utf-8"))
                        self.storage.store_file(
                            vtt_storage_key,
                            vtt_stream,
                            mime_type="text/vtt",
                        )
                        temp_keys.append(vtt_storage_key)

                    if self.lecture_chunk_pipeline is not None:
                        try:
                            self.lecture_chunk_pipeline.process_transcript_segments(
                                lecture_id=lecture.id,
                                course_id=lecture.course_id,
                                segments=transcription_result.segments,
                            )
                        except Exception:  # pragma: no cover - background safety
                            logger.exception("Lecture chunk pipeline failed for lecture %s", lecture_id)

            lecture.status = LectureStatus.completed
            lecture.error_message = None
            db.commit()

            self.storage.delete_file(download_result.storage_key)
        except DownloadError as exc:
            logger.exception("Panopto download failed for lecture %s", lecture_id)
            self._handle_pipeline_failure(db, lecture_id, str(exc), temp_keys=temp_keys)
        except AudioExtractionError as exc:
            logger.exception("Audio extraction failed for lecture %s", lecture_id)
            self._handle_pipeline_failure(db, lecture_id, str(exc), temp_keys=temp_keys)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Audio pipeline failed for lecture %s", lecture_id)
            self._handle_pipeline_failure(db, lecture_id, str(exc), temp_keys=temp_keys)
        finally:
            db.close()

    def _handle_pipeline_failure(
        self,
        db: Session,
        lecture_id: UUID,
        error_message: str,
        temp_keys: Optional[list[str]] = None,
    ) -> None:
        lecture = db.get(Lecture, lecture_id)
        if lecture is None:
            return
        self._cleanup_lecture_assets(lecture)
        db.delete(lecture)
        db.commit()

        if temp_keys:
            for key in temp_keys:
                self.storage.delete_file(key)

    def _ensure_user_link(self, db: Session, user_id: UUID, lecture_id: UUID) -> None:
        exists = (
            db.execute(
                select(UserLecture).where(
                    UserLecture.user_id == user_id,
                    UserLecture.lecture_id == lecture_id,
                )
            )
            .scalars()
            .first()
        )
        if exists is None:
            db.add(UserLecture(user_id=user_id, lecture_id=lecture_id))

    def _cleanup_lecture_assets(self, lecture: Lecture) -> None:
        if lecture.audio_storage_key:
            self.storage.delete_file(lecture.audio_storage_key)
        if lecture.transcript_storage_key:
            self.storage.delete_file(lecture.transcript_storage_key)
        if self.lecture_chunk_pipeline is not None:
            self.lecture_chunk_pipeline.cleanup_lecture(lecture.id)
