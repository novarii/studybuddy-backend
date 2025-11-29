from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Iterable, List, Protocol
from uuid import UUID

import fitz
import imagehash
from PIL import Image

from ..storage import StorageBackend


@dataclass(frozen=True)
class SlideImagePayload:
    """Rendered slide data that downstream pipelines can consume."""

    document_id: UUID
    slide_number: int
    hash_hex: str
    width: int
    height: int
    image_bytes: bytes


@dataclass(frozen=True)
class SlideHashRecord:
    """Metadata recorded alongside each stored hash."""

    document_id: UUID
    slide_number: int


class SlideHashRepository(Protocol):
    """Store/retrieve slide hashes for deduplication."""

    def has_hash(self, hash_hex: str) -> bool:
        raise NotImplementedError

    def add_hash(self, hash_hex: str, record: SlideHashRecord) -> None:
        raise NotImplementedError


class InMemorySlideHashRepository(SlideHashRepository):
    """Simple hash registry useful for local experiments/tests."""

    def __init__(self) -> None:
        self._hashes: dict[str, SlideHashRecord] = {}

    def has_hash(self, hash_hex: str) -> bool:
        return hash_hex in self._hashes

    def add_hash(self, hash_hex: str, record: SlideHashRecord) -> None:
        self._hashes[hash_hex] = record

    def __len__(self) -> int:  # pragma: no cover - convenience
        return len(self._hashes)


class SlideExtractionService:
    """Render PDF slides to images and deduplicate via perceptual hashes."""

    def __init__(
        self,
        storage: StorageBackend,
        hash_repository: SlideHashRepository,
        *,
        zoom: float = 2.0,
    ) -> None:
        self.storage = storage
        self.hash_repository = hash_repository
        self.zoom = zoom

    def extract_unique_slides(
        self,
        document_id: UUID,
        pdf_storage_key: str,
    ) -> List[SlideImagePayload]:
        """Render slides and return only those without previously seen hashes."""

        pdf_bytes = self._read_pdf_bytes(pdf_storage_key)
        try:
            document = fitz.open(stream=pdf_bytes, filetype="pdf")
        except fitz.fitz.FileDataError as exc:
            raise ValueError("Unable to open PDF for slide extraction") from exc

        try:
            return self._render_document(document_id, document)
        finally:
            document.close()

    def _render_document(
        self,
        document_id: UUID,
        document: fitz.Document,
    ) -> List[SlideImagePayload]:
        results: list[SlideImagePayload] = []
        matrix = fitz.Matrix(self.zoom, self.zoom)

        for page_index in range(document.page_count):
            page = document.load_page(page_index)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            hash_hex = str(imagehash.phash(image))
            if self.hash_repository.has_hash(hash_hex):
                continue

            image_bytes = self._serialize_image(image)
            slide_number = page_index + 1
            results.append(
                SlideImagePayload(
                    document_id=document_id,
                    slide_number=slide_number,
                    hash_hex=hash_hex,
                    width=image.width,
                    height=image.height,
                    image_bytes=image_bytes,
                )
            )
            self.hash_repository.add_hash(
                hash_hex,
                SlideHashRecord(document_id=document_id, slide_number=slide_number),
            )
        return results

    def _read_pdf_bytes(self, storage_key: str) -> bytes:
        with self.storage.open_file(storage_key) as pdf_stream:
            return pdf_stream.read()

    def _serialize_image(self, image: Image.Image) -> bytes:
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
