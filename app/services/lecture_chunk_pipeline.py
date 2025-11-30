from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence
from uuid import UUID

from agno.knowledge import Knowledge

from ..agents.knowledge_builder import get_lecture_knowledge
from ..storage import StorageBackend

logger = logging.getLogger(__name__)

KnowledgeFactory = Callable[[], Optional[Knowledge]]


@dataclass(frozen=True)
class TranscriptSegment:
    """Normalized transcript segment extracted from Whisper output."""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class LectureChunk:
    """Chunk of consecutive transcript segments grouped by duration."""

    chunk_index: int
    start: float
    end: float
    segments: List[TranscriptSegment]
    text: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


@dataclass(frozen=True)
class LectureChunkingResult:
    """Aggregated chunk payload ready for persistence."""

    lecture_id: UUID
    course_id: UUID
    chunk_duration_seconds: float
    chunks: List[LectureChunk]


class LectureChunkPipeline:
    """Convert Whisper transcript segments into ~180 second knowledge chunks."""

    def __init__(
        self,
        storage: StorageBackend,
        *,
        chunk_duration_seconds: float = 180.0,
        chunk_storage_prefix: str = "lecture_chunks",
        knowledge_factory: KnowledgeFactory | None = None,
    ) -> None:
        self.storage = storage
        self.chunk_duration_seconds = chunk_duration_seconds
        self.chunk_storage_prefix = chunk_storage_prefix
        self._knowledge_factory = knowledge_factory or get_lecture_knowledge

    def process_transcript_segments(
        self,
        *,
        lecture_id: UUID,
        course_id: UUID,
        segments: List[Dict[str, Any]] | None,
    ) -> None:
        """Build lecture chunks from Whisper segments and ingest them."""

        normalized_segments = self._normalize_segments(segments)
        if not normalized_segments:
            logger.info("Lecture %s has no transcript segments; skipping chunk pipeline", lecture_id)
            self._delete_chunk_artifact(lecture_id)
            return

        chunks = self._build_chunks(normalized_segments)
        if not chunks:
            logger.info("Lecture %s produced no transcript chunks", lecture_id)
            self._delete_chunk_artifact(lecture_id)
            return

        result = LectureChunkingResult(
            lecture_id=lecture_id,
            course_id=course_id,
            chunk_duration_seconds=self.chunk_duration_seconds,
            chunks=chunks,
        )
        self._store_chunk_payload(result)
        self._ingest_into_knowledge(result)

    def cleanup_lecture(self, lecture_id: UUID) -> None:
        """Remove chunk artifacts and vectors for a lecture."""

        self._delete_chunk_artifact(lecture_id)
        self._remove_from_knowledge({"lecture_id": str(lecture_id)})

    def _normalize_segments(
        self,
        segments: Sequence[Dict[str, Any]] | None,
    ) -> List[TranscriptSegment]:
        normalized: List[TranscriptSegment] = []
        if not segments:
            return normalized

        for entry in segments:
            start = self._coerce_float(entry.get("start"))
            end = self._coerce_float(entry.get("end"))
            text = str(entry.get("text") or "").strip()
            if start is None or end is None or not text:
                continue
            if end < start:
                end = start
            normalized.append(TranscriptSegment(start=start, end=end, text=text))

        normalized.sort(key=lambda segment: segment.start)
        return normalized

    def _build_chunks(self, segments: List[TranscriptSegment]) -> List[LectureChunk]:
        chunks: List[LectureChunk] = []
        current: List[TranscriptSegment] = []

        for segment in segments:
            if not current:
                current.append(segment)
                continue

            elapsed = segment.end - current[0].start
            current.append(segment)
            if elapsed >= self.chunk_duration_seconds:
                chunk = self._create_chunk(len(chunks) + 1, current)
                if chunk is not None:
                    chunks.append(chunk)
                current = []

        if current:
            chunk = self._create_chunk(len(chunks) + 1, current)
            if chunk is not None:
                chunks.append(chunk)

        return chunks

    def _create_chunk(
        self,
        chunk_index: int,
        segments: List[TranscriptSegment],
    ) -> LectureChunk | None:
        if not segments:
            return None
        start = segments[0].start
        end = segments[-1].end
        text = " ".join(segment.text for segment in segments if segment.text).strip()
        if not text:
            return None
        return LectureChunk(
            chunk_index=chunk_index,
            start=start,
            end=end,
            segments=list(segments),
            text=text,
        )

    def _store_chunk_payload(self, result: LectureChunkingResult) -> None:
        payload = {
            "lecture_id": str(result.lecture_id),
            "course_id": str(result.course_id),
            "chunk_duration_seconds": result.chunk_duration_seconds,
            "chunk_count": len(result.chunks),
            "chunks": [
                {
                    "chunk_index": chunk.chunk_index,
                    "start": chunk.start,
                    "end": chunk.end,
                    "duration": chunk.duration,
                    "segment_count": len(chunk.segments),
                    "text": chunk.text,
                }
                for chunk in result.chunks
            ],
        }
        buffer = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        self.storage.store_file(
            self._chunk_storage_key(result.lecture_id),
            buffer,
            mime_type="application/json",
        )

    def _ingest_into_knowledge(
        self,
        result: LectureChunkingResult,
    ) -> None:
        if not self._knowledge_factory:
            return
        knowledge = self._knowledge_factory()
        if knowledge is None:
            logger.info("Lecture knowledge not configured; skipping ingestion for %s", result.lecture_id)
            return

        for chunk in result.chunks:
            if not chunk.text:
                continue
            metadata = {
                "lecture_id": str(result.lecture_id),
                "course_id": str(result.course_id),
                "start_seconds": chunk.start,
                "end_seconds": chunk.end,
                "chunk_index": chunk.chunk_index,
            }
            try:
                knowledge.add_content(text_content=chunk.text, metadata=metadata)
            except Exception:  # pragma: no cover - ingestion best-effort
                logger.exception(
                    "Failed to add lecture chunk %s for lecture %s",
                    chunk.chunk_index,
                    result.lecture_id,
                )

    def _remove_from_knowledge(self, metadata: Dict[str, str]) -> None:
        if not self._knowledge_factory:
            return
        knowledge = self._knowledge_factory()
        if knowledge is None:
            return
        try:
            knowledge.remove_vectors_by_metadata(metadata)
        except Exception:  # pragma: no cover - defensive
            logger.exception("Failed to remove lecture knowledge rows for %s", metadata)

    def _chunk_storage_key(self, lecture_id: UUID) -> str:
        return f"{self.chunk_storage_prefix}/{lecture_id}.json"

    def _delete_chunk_artifact(self, lecture_id: UUID) -> None:
        try:
            self.storage.delete_file(self._chunk_storage_key(lecture_id))
        except FileNotFoundError:
            return

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
