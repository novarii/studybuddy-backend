from __future__ import annotations

import io
import json
import logging
from typing import Dict
from uuid import UUID

from ..agents.pdf_description_agent import SlideDescriptionAgent
from ..storage import StorageBackend
from .pdf_slide_chunks_service import SlideChunkingService
from .pdf_slides_service import InMemorySlideHashRepository, SlideExtractionService, SlideImagePayload

logger = logging.getLogger(__name__)


class DocumentChunkPipeline:
    """Run slide chunking for uploaded documents and persist the results."""

    def __init__(
        self,
        storage: StorageBackend,
        *,
        chunk_storage_prefix: str = "document_chunks",
    ) -> None:
        self.storage = storage
        self.chunk_storage_prefix = chunk_storage_prefix

    def process_document(self, document_id: UUID, pdf_storage_key: str) -> None:
        """Background entry point: extract slides, describe them, and write chunk payload."""

        chunk_service = self._build_chunking_service()
        try:
            result = chunk_service.generate_chunks(document_id, pdf_storage_key)
        except Exception:  # pragma: no cover - defensive logging for background task
            logger.exception("Slide chunk generation failed for document %s", document_id)
            return

        chunk_storage_key = self._chunk_storage_key(document_id)
        if not result.chunks:
            logger.info("No slide chunks produced for document %s", document_id)
            self.storage.delete_file(chunk_storage_key)
            return

        slide_lookup: Dict[int, SlideImagePayload] = {slide.slide_number: slide for slide in result.slides}
        chunk_records = []
        for chunk in result.chunks:
            slide = slide_lookup.get(chunk.slide_number)
            chunk_records.append(
                {
                    "document_id": str(chunk.document_id),
                    "slide_number": chunk.slide_number,
                    "hash_hex": chunk.hash_hex,
                    "width": slide.width if slide else None,
                    "height": slide.height if slide else None,
                    "slide_type": chunk.content.slide_type.value,
                    "text_content": chunk.content.text_content,
                    "images_description": chunk.content.images_description,
                    "diagrams_and_figures_description": chunk.content.diagrams_and_figures_description,
                    "chunk_text": chunk.chunk_text,
                }
            )

        payload = {
            "document_id": str(document_id),
            "chunks": chunk_records,
        }
        buffer = io.BytesIO(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
        self.storage.store_file(
            chunk_storage_key,
            buffer,
            mime_type="application/json",
        )
        logger.info("Stored %s slide chunks for document %s", len(chunk_records), document_id)

    def _chunk_storage_key(self, document_id: UUID) -> str:
        return f"{self.chunk_storage_prefix}/{document_id}.json"

    def _build_chunking_service(self) -> SlideChunkingService:
        extractor = SlideExtractionService(self.storage, InMemorySlideHashRepository())
        description_agent = SlideDescriptionAgent()
        return SlideChunkingService(extractor, description_agent)
