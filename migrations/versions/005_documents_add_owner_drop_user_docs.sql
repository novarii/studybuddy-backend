ALTER TABLE documents
ADD COLUMN IF NOT EXISTS owner_id UUID;

UPDATE documents d
SET owner_id = ud.user_id
FROM user_documents ud
WHERE ud.document_id = d.id
  AND d.owner_id IS NULL;

ALTER TABLE documents
ALTER COLUMN owner_id SET NOT NULL;

ALTER TABLE documents
DROP CONSTRAINT IF EXISTS uq_course_checksum;

ALTER TABLE documents
ADD CONSTRAINT uq_owner_course_checksum UNIQUE (owner_id, course_id, checksum);

DROP TABLE IF EXISTS user_documents;

DROP INDEX IF EXISTS idx_user_documents_document_id;
