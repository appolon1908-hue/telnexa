BEGIN;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS content varchar(5000);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS route_decision_id varchar(36);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS dispatch_job_id varchar(36);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS submitted_at timestamptz;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS delivered_at timestamptz;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS terminal_at timestamptz;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS failure_code varchar(80);
ALTER TABLE messages ADD COLUMN IF NOT EXISTS submission_certainty varchar(20);
CREATE INDEX IF NOT EXISTS ix_messages_route_decision_id ON messages(route_decision_id);
CREATE INDEX IF NOT EXISTS ix_messages_dispatch_job_id ON messages(dispatch_job_id);

ALTER TABLE providers ADD COLUMN IF NOT EXISTS adapter_type varchar(40) NOT NULL DEFAULT 'simulator';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS credential_reference varchar(255);
ALTER TABLE providers ADD COLUMN IF NOT EXISTS environment varchar(30) NOT NULL DEFAULT 'simulator';
ALTER TABLE providers ADD COLUMN IF NOT EXISTS base_url varchar(500);
ALTER TABLE providers ADD COLUMN IF NOT EXISTS routing_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS max_inflight integer NOT NULL DEFAULT 1;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS connect_timeout_ms integer NOT NULL DEFAULT 1000;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS request_timeout_ms integer NOT NULL DEFAULT 5000;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS last_success_at timestamptz;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS last_failure_at timestamptz;
ALTER TABLE providers ADD COLUMN IF NOT EXISTS failure_streak integer NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS sms_dispatch_jobs (
 id varchar(36) PRIMARY KEY, tenant_id varchar(36) NOT NULL, message_id varchar(36) NOT NULL UNIQUE,
 state varchar(30) NOT NULL DEFAULT 'queued', priority integer NOT NULL DEFAULT 0,
 available_at timestamptz NOT NULL, attempt_count integer NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
 max_attempts integer NOT NULL DEFAULT 3, lease_owner varchar(120), lease_expires_at timestamptz,
 selected_provider_id varchar(36), route_decision_id varchar(36), last_error_class varchar(80),
 last_error_code varchar(120), last_error_at timestamptz, created_at timestamptz NOT NULL,
 updated_at timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sms_dispatch_jobs_state_available ON sms_dispatch_jobs(state, available_at);
CREATE INDEX IF NOT EXISTS ix_sms_dispatch_jobs_tenant_state ON sms_dispatch_jobs(tenant_id, state);
CREATE INDEX IF NOT EXISTS ix_sms_dispatch_jobs_provider_state ON sms_dispatch_jobs(selected_provider_id, state);
CREATE INDEX IF NOT EXISTS ix_sms_dispatch_jobs_lease ON sms_dispatch_jobs(lease_expires_at);

CREATE TABLE IF NOT EXISTS sms_dispatch_attempts (
 id varchar(36) PRIMARY KEY, job_id varchar(36) NOT NULL, message_id varchar(36) NOT NULL,
 tenant_id varchar(36) NOT NULL, attempt_number integer NOT NULL, provider_id varchar(36) NOT NULL,
 adapter_type varchar(40) NOT NULL, route_version integer NOT NULL, request_fingerprint varchar(64) NOT NULL,
 started_at timestamptz NOT NULL, completed_at timestamptz, outcome varchar(30),
 provider_message_id varchar(120), http_status integer, provider_code varchar(120), error_class varchar(80),
 latency_ms integer, secret_free_evidence_json json NOT NULL DEFAULT '{}', UNIQUE(job_id, attempt_number));
CREATE INDEX IF NOT EXISTS ix_sms_dispatch_attempts_tenant ON sms_dispatch_attempts(tenant_id);
CREATE INDEX IF NOT EXISTS ix_sms_dispatch_attempts_message ON sms_dispatch_attempts(message_id);

CREATE TABLE IF NOT EXISTS sms_route_decisions (
 id varchar(36) PRIMARY KEY, message_id varchar(36) NOT NULL, tenant_id varchar(36) NOT NULL,
 destination_prefix varchar(32) NOT NULL, country varchar(2) NOT NULL, selected_provider_id varchar(36),
 selected_route_id varchar(36), route_version integer, candidate_summary json NOT NULL DEFAULT '[]',
 provider_rate_snapshot json NOT NULL DEFAULT '{}', sell_rate_snapshot json NOT NULL DEFAULT '{}',
 decision_hash varchar(64) NOT NULL UNIQUE, created_at timestamptz NOT NULL);
CREATE INDEX IF NOT EXISTS ix_sms_route_decisions_tenant ON sms_route_decisions(tenant_id);

CREATE TABLE IF NOT EXISTS sms_provider_event_inbox (
 id varchar(36) PRIMARY KEY, source varchar(40) NOT NULL, source_key_id varchar(120) NOT NULL,
 event_id varchar(120) NOT NULL, event_type varchar(20) NOT NULL, provider_message_id varchar(120),
 message_id varchar(36), tenant_id varchar(36), payload_hash varchar(64) NOT NULL,
 normalized_payload json NOT NULL, occurred_at timestamptz, received_at timestamptz NOT NULL,
 state varchar(30) NOT NULL DEFAULT 'pending', attempts integer NOT NULL DEFAULT 0,
 last_error varchar(500), UNIQUE(source,event_id));
CREATE INDEX IF NOT EXISTS ix_sms_provider_event_inbox_state ON sms_provider_event_inbox(state,received_at);
CREATE INDEX IF NOT EXISTS ix_sms_provider_event_inbox_tenant ON sms_provider_event_inbox(tenant_id);
CREATE INDEX IF NOT EXISTS ix_sms_provider_event_inbox_provider_message ON sms_provider_event_inbox(provider_message_id);

CREATE TABLE IF NOT EXISTS sms_provider_event_attempts (
 id varchar(36) PRIMARY KEY, inbox_id varchar(36) NOT NULL, tenant_id varchar(36), attempt_number integer NOT NULL,
 outcome varchar(30) NOT NULL, error varchar(500), started_at timestamptz NOT NULL, completed_at timestamptz,
 UNIQUE(inbox_id,attempt_number));
CREATE INDEX IF NOT EXISTS ix_sms_provider_event_attempts_tenant ON sms_provider_event_attempts(tenant_id);

CREATE TABLE IF NOT EXISTS sms_reconciliation_cases (
 id varchar(36) PRIMARY KEY, tenant_id varchar(36), message_id varchar(36), case_type varchar(50) NOT NULL,
 reference_id varchar(120) NOT NULL, state varchar(30) NOT NULL DEFAULT 'open', evidence json NOT NULL DEFAULT '{}',
 resolution json NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL,
 UNIQUE(case_type,reference_id));
CREATE INDEX IF NOT EXISTS ix_sms_reconciliation_cases_tenant ON sms_reconciliation_cases(tenant_id);
CREATE INDEX IF NOT EXISTS ix_sms_reconciliation_cases_state ON sms_reconciliation_cases(state);

CREATE TABLE IF NOT EXISTS sms_production_canary_gates (
 id varchar(36) PRIMARY KEY, gate_key varchar(120) NOT NULL UNIQUE, stage varchar(40) NOT NULL,
 allowed_tenant varchar(36) NOT NULL, allowed_sender varchar(20) NOT NULL, allowed_destinations json NOT NULL,
 max_submissions integer NOT NULL, reserved_count integer NOT NULL DEFAULT 0, claimed_count integer NOT NULL DEFAULT 0,
 expires_at timestamptz NOT NULL, approval_reference varchar(255) NOT NULL, approved_by varchar(120) NOT NULL,
 enabled boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL, updated_at timestamptz NOT NULL);

DROP TRIGGER IF EXISTS sms_route_decisions_immutable ON sms_route_decisions;
CREATE TRIGGER sms_route_decisions_immutable BEFORE UPDATE OR DELETE ON sms_route_decisions
FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

CREATE OR REPLACE FUNCTION protect_dispatch_attempt_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.id <> NEW.id OR OLD.job_id <> NEW.job_id OR OLD.message_id <> NEW.message_id
    OR OLD.tenant_id <> NEW.tenant_id OR OLD.attempt_number <> NEW.attempt_number
    OR OLD.provider_id <> NEW.provider_id OR OLD.adapter_type <> NEW.adapter_type
    OR OLD.route_version <> NEW.route_version OR OLD.request_fingerprint <> NEW.request_fingerprint
    OR OLD.started_at <> NEW.started_at THEN
   RAISE EXCEPTION 'dispatch attempt identity is immutable';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS sms_dispatch_attempt_identity_immutable ON sms_dispatch_attempts;
CREATE TRIGGER sms_dispatch_attempt_identity_immutable BEFORE UPDATE ON sms_dispatch_attempts
FOR EACH ROW EXECUTE FUNCTION protect_dispatch_attempt_identity();

DO $$ DECLARE t text; BEGIN
 FOREACH t IN ARRAY ARRAY['sms_dispatch_jobs','sms_dispatch_attempts','sms_route_decisions','sms_provider_event_inbox','sms_provider_event_attempts','sms_reconciliation_cases'] LOOP
  EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
  EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
  EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'', true), ''''))', t);
 END LOOP;
END $$;
COMMIT;
