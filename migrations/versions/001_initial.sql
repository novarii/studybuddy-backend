CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Ensure a basic users table exists so that FK constraints can be created even in local dev environments.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = 'users'
    ) THEN
        CREATE TABLE public.users (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
    END IF;
END $$;

CREATE TYPE lecture_status AS ENUM ('pending', 'downloading', 'completed', 'failed');
CREATE TYPE document_status AS ENUM ('uploaded', 'failed');

CREATE TABLE lectures (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL,
    panopto_session_id TEXT,
    panopto_url TEXT NOT NULL,
    title TEXT,
    audio_storage_key TEXT,
    transcript_storage_key TEXT,
    duration_seconds INT,
    status lecture_status NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_course_session UNIQUE (course_id, panopto_session_id)
);

CREATE TABLE documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    course_id UUID NOT NULL,
    filename TEXT NOT NULL,
    storage_key TEXT NOT NULL,
    checksum TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes BIGINT NOT NULL,
    page_count INT,
    description TEXT,
    status document_status NOT NULL DEFAULT 'uploaded',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_course_checksum UNIQUE (course_id, checksum)
);

CREATE TABLE user_lectures (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    lecture_id UUID NOT NULL REFERENCES lectures(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, lecture_id)
);

CREATE TABLE user_documents (
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, document_id)
);

CREATE INDEX idx_lectures_course_id ON lectures(course_id);
CREATE INDEX idx_documents_course_id ON documents(course_id);
CREATE INDEX idx_documents_checksum ON documents(checksum);
CREATE INDEX idx_user_lectures_lecture_id ON user_lectures(lecture_id);
CREATE INDEX idx_user_documents_document_id ON user_documents(document_id);

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_lectures_updated_at
BEFORE UPDATE ON lectures
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TRIGGER trg_documents_updated_at
BEFORE UPDATE ON documents
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
