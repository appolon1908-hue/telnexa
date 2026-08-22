ALTER TABLE messages ADD COLUMN IF NOT EXISTS request_hash VARCHAR(64);
UPDATE messages SET request_hash = '' WHERE request_hash IS NULL;
ALTER TABLE messages ALTER COLUMN request_hash SET NOT NULL;

-- Rollback (operator-controlled): ALTER TABLE messages DROP COLUMN request_hash;
