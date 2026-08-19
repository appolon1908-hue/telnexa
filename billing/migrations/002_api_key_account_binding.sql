ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS account_id varchar(36);
CREATE INDEX IF NOT EXISTS ix_api_keys_account_id ON api_keys(account_id);
