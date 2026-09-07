-- Bind dispatch authorization and provider callbacks to their exact durable authorities.
ALTER TABLE providers
  ADD COLUMN IF NOT EXISTS dlr_source_key_id varchar(120);

ALTER TABLE providers
  DROP CONSTRAINT IF EXISTS providers_dlr_source_key_id_key;

ALTER TABLE providers
  ADD CONSTRAINT providers_dlr_source_key_id_key UNIQUE(dlr_source_key_id);

ALTER TABLE sms_dispatch_jobs
  ADD COLUMN IF NOT EXISTS canary_gate_id varchar(36);

CREATE INDEX IF NOT EXISTS ix_sms_dispatch_jobs_canary_gate_id
  ON sms_dispatch_jobs(canary_gate_id);

ALTER TABLE sms_provider_event_inbox
  DROP CONSTRAINT IF EXISTS sms_provider_event_inbox_source_event_id_key;

ALTER TABLE sms_provider_event_inbox
  DROP CONSTRAINT IF EXISTS sms_provider_event_inbox_source_key_id_event_id_key;

ALTER TABLE sms_provider_event_inbox
  ADD CONSTRAINT sms_provider_event_inbox_source_key_id_event_id_key
  UNIQUE(source_key_id, event_id);
