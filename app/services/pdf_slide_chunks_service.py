from __future__ import annotations

from dataclasses import dataclass
from typing import List
from uuid import UUID

from ..agents.pdf_description_agent import SlideContentWithNumber, SlideDescriptionAgent
from .pdf_slides_service import SlideExtractionService, SlideImagePayload


@dataclass(frozen=True)
class SlideChunk:
    """Chunk metadata tying slide hashes to their AI-generated content."""

    document_id: UUID
    slide_number: int
    hash_hex: str
    content: SlideContentWithNumber

    @property
    def chunk_text(self) -> str:
        """Return the normalized chunk string for downstream ingestion."""

        return self.content.as_chunk()


@dataclass(frozen=True)
class SlideChunkingResult:
    """Aggregate payload that bundles extraction/descriptions together."""

    document_id: UUID
    slides: List[SlideImagePayload]
    descriptions: List[SlideContentWithNumber]
    chunks: List[SlideChunk]

    def chunk_texts(self) -> List[str]:
        return [chunk.chunk_text for chunk in self.chunks]


class SlideChunkingService:
    """High-level orchestrator that renders slides and produces description chunks."""

    def __init__(
        self,
        extractor: SlideExtractionService,
        description_agent: SlideDescriptionAgent,
    ) -> None:
        self.extractor = extractor
        self.description_agent = description_agent

    def generate_chunks(self, document_id: UUID, pdf_storage_key: str) -> SlideChunkingResult:
        """Extract unique slides, describe them, and return chunk metadata."""

        slides = self.extractor.extract_unique_slides(document_id, pdf_storage_key)
        if not slides:
            return SlideChunkingResult(
                document_id=document_id,
                slides=[],
                descriptions=[],
                chunks=[],
            )

        descriptions = self.description_agent.describe_slides(slides)
        if len(descriptions) != len(slides):  # Defensive guard against mismatched responses.
            raise ValueError(
                f"Expected {len(slides)} slide descriptions, received {len(descriptions)}."
            )

        chunks: list[SlideChunk] = []
        for slide, description in zip(slides, descriptions):
            chunks.append(
                SlideChunk(
                    document_id=document_id,
                    slide_number=slide.slide_number,
                    hash_hex=slide.hash_hex,
                    content=description,
                )
            )

        return SlideChunkingResult(
            document_id=document_id,
            slides=slides,
            descriptions=descriptions,
            chunks=chunks,
        )
