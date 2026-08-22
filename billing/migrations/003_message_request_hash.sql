BEGIN;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS request_hash varchar(64);
UPDATE messages SET request_hash = 'legacy:' || md5(destination || E'\n' || sender || E'\n' || content_hash) WHERE request_hash IS NULL;
ALTER TABLE messages ALTER COLUMN request_hash SET NOT NULL;
COMMIT;
