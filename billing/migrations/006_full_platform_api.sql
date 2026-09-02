CREATE TABLE IF NOT EXISTS service_accounts (
  id varchar(36) PRIMARY KEY,
  tenant_id varchar(36) NOT NULL,
  name varchar(160) NOT NULL,
  client_id varchar(120) NOT NULL UNIQUE,
  secret_hash varchar(255) NOT NULL,
  scopes text NOT NULL,
  enabled boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now(),
  rotated_at timestamptz,
  CONSTRAINT uq_service_account_tenant_client UNIQUE (tenant_id, client_id)
);

CREATE INDEX IF NOT EXISTS ix_service_accounts_tenant
  ON service_accounts (tenant_id, created_at DESC);

ALTER TABLE service_accounts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tenant_isolation ON service_accounts;
CREATE POLICY tenant_isolation ON service_accounts
  USING (tenant_id = nullif(current_setting('app.tenant_id', true), ''))
  WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), ''));
