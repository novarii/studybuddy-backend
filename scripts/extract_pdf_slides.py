#!/usr/bin/env python3
from __future__ import annotations

import argparse
import uuid
from pathlib import Path

from app.services.pdf_slides_service import (
    InMemorySlideHashRepository,
    SlideExtractionService,
)
from app.storage import LocalStorageBackend


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render a PDF into slide images, hash them, and deduplicate locally."
    )
    parser.add_argument("pdf", type=Path, help="Path to the PDF file to extract.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Optional directory to write unique slide images for inspection.",
    )
    args = parser.parse_args()

    pdf_path: Path = args.pdf.expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    storage_root = pdf_path.parent
    storage = LocalStorageBackend(storage_root)
    repo = InMemorySlideHashRepository()
    extractor = SlideExtractionService(storage, repo)

    document_id = uuid.uuid4()
    slides = extractor.extract_unique_slides(document_id, pdf_path.name)
    print(f"Extracted {len(slides)} unique slides from {pdf_path.name}")

    if args.output_dir:
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for slide in slides:
            target = args.output_dir / f"{slide.slide_number:03d}_{slide.hash_hex}.png"
            target.write_bytes(slide.image_bytes)
        print(f"Wrote slides to {args.output_dir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
