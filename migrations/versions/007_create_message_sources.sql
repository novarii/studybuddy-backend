-- Message sources table for persisting RAG citations
-- Enables citations to work when loading message history (page refresh, session switch)

-- Ensure ai schema exists (created by Agno, but be safe)
CREATE SCHEMA IF NOT EXISTS ai;

CREATE TABLE IF NOT EXISTS ai.message_sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    message_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    chunk_number INT NOT NULL,
    content_preview TEXT,
    document_id UUID,
    slide_number INT,
    lecture_id UUID,
    start_seconds FLOAT,
    end_seconds FLOAT,
    course_id UUID,
    owner_id UUID,
    title TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_message_source UNIQUE (message_id, source_id)
);

CREATE INDEX IF NOT EXISTS idx_message_sources_message_id ON ai.message_sources(message_id);
CREATE INDEX IF NOT EXISTS idx_message_sources_session_id ON ai.message_sources(session_id);
