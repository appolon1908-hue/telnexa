-- Additive, fail-closed post-canary production authorization and delivery policy.
CREATE TABLE IF NOT EXISTS sms_production_authorizations (
  id varchar(36) PRIMARY KEY,
  change_id varchar(120) NOT NULL UNIQUE,
  idempotency_key varchar(180) NOT NULL UNIQUE,
  request_sha256 varchar(64) NOT NULL,
  tenant_id varchar(36) NOT NULL,
  environment varchar(20) NOT NULL DEFAULT 'production',
  production_owner varchar(120) NOT NULL,
  approved_senders json NOT NULL,
  approved_destinations json NOT NULL,
  approved_categories json NOT NULL,
  per_minute_segments integer NOT NULL CHECK (per_minute_segments > 0),
  per_hour_segments integer NOT NULL CHECK (per_hour_segments > 0),
  per_day_segments integer NOT NULL CHECK (per_day_segments > 0),
  provider_id varchar(36) NOT NULL,
  billing_account_id varchar(36) NOT NULL,
  max_total_spend_minor integer NOT NULL CHECK (max_total_spend_minor > 0),
  spending_currency varchar(3) NOT NULL,
  account_grain varchar(40) NOT NULL CHECK (account_grain = 'billing_account'),
  valid_from timestamptz NOT NULL,
  valid_until timestamptz NOT NULL,
  monitoring_owner varchar(120) NOT NULL,
  escalation_owner varchar(120) NOT NULL,
  rollback_owner varchar(120) NOT NULL,
  kill_switch_procedure varchar(500) NOT NULL,
  approved_release_sha varchar(64) NOT NULL,
  approved_by varchar(120) NOT NULL,
  authorization_timestamp timestamptz NOT NULL,
  review_at timestamptz NOT NULL,
  reason varchar(500) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (environment = 'production'),
  CHECK (valid_until > valid_from),
  CHECK (review_at >= authorization_timestamp)
);
CREATE INDEX IF NOT EXISTS ix_sms_production_authorizations_tenant
  ON sms_production_authorizations (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS sms_production_authorization_revocations (
  id varchar(36) PRIMARY KEY,
  authorization_id varchar(36) NOT NULL UNIQUE
    REFERENCES sms_production_authorizations(id),
  idempotency_key varchar(180) NOT NULL UNIQUE,
  revoked_by varchar(120) NOT NULL,
  correlation_id varchar(36) NOT NULL,
  reason varchar(500) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS sms_delivery_policies (
  id varchar(36) PRIMARY KEY,
  tenant_id varchar(36) NOT NULL,
  environment varchar(20) NOT NULL DEFAULT 'production',
  policy_version integer NOT NULL DEFAULT 1 CHECK (policy_version > 0),
  enabled boolean NOT NULL DEFAULT false,
  mode varchar(40) NOT NULL DEFAULT 'SAFE'
    CHECK (mode IN ('SAFE','TRANSACTIONAL_CANARY','TRANSACTIONAL_PRODUCTION','CAMPAIGN_PRODUCTION')),
  authorization_id varchar(36) REFERENCES sms_production_authorizations(id),
  authorization_change_id varchar(120),
  approved_senders json NOT NULL DEFAULT '[]',
  approved_destinations json NOT NULL DEFAULT '[]',
  recipient_scope varchar(40) NOT NULL DEFAULT 'exact_allowlist'
    CHECK (recipient_scope = 'exact_allowlist'),
  transaction_categories json NOT NULL DEFAULT '[]',
  per_minute_segments integer NOT NULL DEFAULT 0 CHECK (per_minute_segments >= 0),
  per_hour_segments integer NOT NULL DEFAULT 0 CHECK (per_hour_segments >= 0),
  per_day_segments integer NOT NULL DEFAULT 0 CHECK (per_day_segments >= 0),
  provider_id varchar(36),
  billing_account_id varchar(36),
  max_total_spend_minor integer NOT NULL DEFAULT 0 CHECK (max_total_spend_minor >= 0),
  spending_currency varchar(3),
  account_grain varchar(40) NOT NULL DEFAULT 'billing_account'
    CHECK (account_grain = 'billing_account'),
  valid_from timestamptz,
  valid_until timestamptz,
  approved_by varchar(120),
  activated_by varchar(120),
  system_kill_switch boolean NOT NULL DEFAULT true,
  tenant_kill_switch boolean NOT NULL DEFAULT true,
  sender_kill_switches json NOT NULL DEFAULT '[]',
  reason varchar(500) NOT NULL DEFAULT 'not authorized',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, environment),
  CHECK (environment = 'production')
);
CREATE INDEX IF NOT EXISTS ix_sms_delivery_policies_authorization
  ON sms_delivery_policies (authorization_id);

CREATE TABLE IF NOT EXISTS sms_system_controls (
  id varchar(36) PRIMARY KEY,
  environment varchar(20) NOT NULL UNIQUE DEFAULT 'production'
    CHECK (environment = 'production'),
  control_version integer NOT NULL DEFAULT 1 CHECK (control_version > 0),
  kill_switch boolean NOT NULL DEFAULT true,
  reason varchar(500) NOT NULL DEFAULT 'not authorized',
  actor varchar(120) NOT NULL DEFAULT 'system',
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE sms_dispatch_jobs
  ADD COLUMN IF NOT EXISTS production_policy_id varchar(36);
CREATE INDEX IF NOT EXISTS ix_sms_dispatch_jobs_production_policy
  ON sms_dispatch_jobs (production_policy_id);

-- Authorization rows are immutable. Revocation is a separate append-only row.
CREATE OR REPLACE FUNCTION reject_sms_production_authorization_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'SMS production authorizations are immutable'; END $$;
DROP TRIGGER IF EXISTS sms_production_authorizations_immutable
  ON sms_production_authorizations;
CREATE TRIGGER sms_production_authorizations_immutable
  BEFORE UPDATE OR DELETE ON sms_production_authorizations
  FOR EACH ROW EXECUTE FUNCTION reject_sms_production_authorization_mutation();

CREATE OR REPLACE FUNCTION reject_sms_authorization_revocation_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'SMS production authorization revocations are immutable'; END $$;
DROP TRIGGER IF EXISTS sms_production_authorization_revocations_immutable
  ON sms_production_authorization_revocations;
CREATE TRIGGER sms_production_authorization_revocations_immutable
  BEFORE UPDATE OR DELETE ON sms_production_authorization_revocations
  FOR EACH ROW EXECUTE FUNCTION reject_sms_authorization_revocation_mutation();
