# PDF Vector Pipeline Plan

## Goal
Ingest PDF lecture slides into a vector store with minimal yet high-signal text chunks by deduplicating slide images, generating rich descriptions via an AI agent, and performing custom chunking + embedding with near-duplicate removal.

## Current Understanding
1. **Slide extraction & hashing**
   - Use **PyMuPDF** (fitz) to render each PDF page/slide to an image.
   - Compute a **perceptual hash (pHash)** for each rendered slide so that visually similar slides collide, allowing cost-free duplicate detection.
   - Store both the hash and the rendered image reference; use the hash to skip slides we’ve already processed, reducing AI/embedding costs.
2. **Deduplicate slides** by checking the pHash against existing records; only novel slides proceed further.
3. **AI description generation**:
   - Implement an agent under `app/agents/pdf_description_agent.py`.
   - Use an output schema defined with **Pydantic BaseModel** to force consistent fields:
     - `SlideContent` model with `text_content`, `images_description`, and `diagrams_and_figures_description`.
     - A `SlideType` enum (`title` and `content`) associated to each `SlideContent`.
     - Wrap the AI output in `SlideContentWithNumber` after inference to attach the original slide number (the agent itself fills only `SlideContent`, we add the number post-processing).
   - We combine subfields into a single chunk string per slide (including the slide number and type) before ingestion.
   - The agent will run via the Agno framework (details pending), but the schema + file location are fixed.
4. **Custom chunking**: run the generated text through our custom chunking logic to split descriptions into semantically coherent pieces sized for embeddings.
5. **Embedding + filtering**: compute embeddings for each chunk, then remove chunks with high cosine similarity so the final vector table only stores distinct, high-information entries.
6. **Persist** the curated chunks + embeddings into the vector table for later semantic search on slides.

## Next Steps
Please provide details about:
- Agno agent configuration and prompts for slide understanding (model, context windows, system instructions).
- Exact `SlideContent` + `SlideType` schema requirements (validation rules, default handling) and how `SlideContentWithNumber` should be serialized.
- Custom chunking logic specifics.
- Embedding model + vector DB schema, including cosine similarity thresholds for deduplication.

## Related Docs
- [System/project_architecture.md](../System/project_architecture.md)
- [System/database_schema.md](../System/database_schema.md)
