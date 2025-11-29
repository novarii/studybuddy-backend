#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.agents.pdf_description_agent import SlideDescriptionAgent
from app.services.pdf_slides_service import (
    InMemorySlideHashRepository,
    SlideExtractionService,
)
from app.storage import LocalStorageBackend


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the slide extraction + description pipeline on a PDF and write chunks to disk."
    )
    parser.add_argument("pdf", type=Path, help="Path to the PDF file to process.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("slide_chunks.json"),
        help="Where to write the resulting chunks (default: slide_chunks.json).",
    )
    args = parser.parse_args()

    pdf_path = args.pdf.expanduser().resolve()
    if not pdf_path.exists():
        raise SystemExit(f"PDF not found: {pdf_path}")

    storage_root = pdf_path.parent
    storage = LocalStorageBackend(storage_root)
    hash_repo = InMemorySlideHashRepository()
    extractor = SlideExtractionService(storage, hash_repo)
    agent = SlideDescriptionAgent()

    document_id = uuid.uuid4()
    slides = extractor.extract_unique_slides(document_id, pdf_path.name)
    if not slides:
        print("No unique slides found.")
        return 0

    descriptions = agent.describe_slides(slides)
    chunks = [desc.as_chunk() for desc in descriptions]

    output_path = args.output.resolve()
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(chunks, f, indent=2, ensure_ascii=False)

    print(f"Processed {len(descriptions)} slides. Chunks written to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
