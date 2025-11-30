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
4. **Custom chunking/reader integration**: plug our bespoke chunking logic into Agno by either
   - implementing a `ChunkingStrategy` subclass (docs’ `CustomChunking`) and wrapping our slides in a custom reader, or
   - writing a custom reader that emits `Document` objects built from our slide descriptions (text + metadata) so Agno’s Knowledge system can feed them into the chunking strategy we need.
5. **Embedding + filtering**:
   - Compute embeddings for each chunk.
   - Remove chunks with high cosine similarity so the final vector table only stores distinct, high-information entries.
6. **Persist** the curated chunks + embeddings into a PgVector-backed table and link the resulting rows to an Agno `Knowledge` object so agents can run semantic search against lecture slides. Each `knowledge.add_content` call must include metadata for filtering (at minimum `owner_id` and `course_id` so we can scope results to the user’s uploads/course). Agno’s `Knowledge(vector_db=PgVector(...))` wiring supports both synchronous and async ingestion, and we can pass a custom reader/chunking strategy when calling `add_content`/`add_content_async`.

## Next Steps
Please provide details about:
- Agno agent configuration and prompts for slide understanding (model, context windows, system instructions).
- Exact `SlideContent` + `SlideType` schema requirements (validation rules, default handling) and how `SlideContentWithNumber` should be serialized.
- We bypass readers entirely and push `Document`s straight into the vector DB via repeated `knowledge.add_content(text_content=chunk, metadata={"owner_id": ..., "course_id": ...})` calls?
- Embedding model + vector DB schema, including cosine similarity thresholds for deduplication. (We plan to use Voyage 3-lite for embeddings—need to confirm whether Agno already ships a Voyage embedder or if we should implement a custom embedder wrapper.)
- PgVector schema details for the slide embeddings table (columns for chunk text, embedding vector, metadata references) and whether we should use Agno’s built-in `Knowledge` + `PgVector` integration (see Agno docs on `Knowledge(vector_db=PgVector(...))`) or manage ingestion manually.

## Related Docs
- [System/project_architecture.md](../System/project_architecture.md)
- [System/database_schema.md](../System/database_schema.md)
