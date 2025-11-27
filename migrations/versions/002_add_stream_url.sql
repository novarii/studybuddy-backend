ALTER TABLE lectures ADD COLUMN IF NOT EXISTS stream_url TEXT;
UPDATE lectures SET stream_url = panopto_url WHERE stream_url IS NULL;
ALTER TABLE lectures ALTER COLUMN stream_url SET NOT NULL;
