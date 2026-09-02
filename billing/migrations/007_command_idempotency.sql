CREATE TABLE IF NOT EXISTS command_idempotency (
  id varchar(36) PRIMARY KEY,
  tenant_id varchar(36) NOT NULL,
  caller_identity varchar(180) NOT NULL,
  resource varchar(160) NOT NULL,
  action varchar(80) NOT NULL,
  api_version varchar(20) NOT NULL,
  idempotency_key varchar(180) NOT NULL,
  semantic_sha256 varchar(64) NOT NULL,
  status_code integer NOT NULL,
  resource_id varchar(36),
  response_json jsonb,
  response_ciphertext text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_command_idempotency_identity UNIQUE (
    tenant_id,
    caller_identity,
    resource,
    action,
    api_version,
    idempotency_key
  ),
  CONSTRAINT ck_command_idempotency_response CHECK (
    (response_json IS NOT NULL AND response_ciphertext IS NULL)
    OR (response_json IS NULL AND response_ciphertext IS NOT NULL)
  )
);

CREATE INDEX IF NOT EXISTS ix_command_idempotency_tenant_created
  ON command_idempotency (tenant_id, created_at DESC);

ALTER TABLE command_idempotency ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON command_idempotency;
CREATE POLICY tenant_isolation ON command_idempotency
  USING (tenant_id = nullif(current_setting('app.tenant_id', true), ''))
  WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), ''));
