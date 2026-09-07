-- Additive only. Legacy acceptances are not fabricated from mutable message state.
CREATE TABLE IF NOT EXISTS sms_acceptance_receipts (
  message_id varchar(36) PRIMARY KEY REFERENCES messages(id),
  tenant_id varchar(36) NOT NULL,
  idempotency_key varchar(180) NOT NULL,
  request_hash varchar(64) NOT NULL,
  response_text text NOT NULL,
  client_reference varchar(120),
  campaign_id varchar(36),
  category varchar(20) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_sms_acceptance_tenant_reference
  ON sms_acceptance_receipts (tenant_id, client_reference);
CREATE INDEX IF NOT EXISTS ix_messages_tenant_created
  ON messages (tenant_id, created_at);
-- Preserve original acceptance text at the database boundary.
CREATE OR REPLACE FUNCTION reject_sms_acceptance_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'SMS acceptance receipts are immutable'; END $$;
DROP TRIGGER IF EXISTS sms_acceptance_receipts_immutable ON sms_acceptance_receipts;
CREATE TRIGGER sms_acceptance_receipts_immutable
  BEFORE UPDATE OR DELETE ON sms_acceptance_receipts
  FOR EACH ROW EXECUTE FUNCTION reject_sms_acceptance_mutation();

CREATE TABLE IF NOT EXISTS sms_inbound_bindings (
  source_key_id varchar(120) NOT NULL,
  destination varchar(32) NOT NULL,
  number_id varchar(36) NOT NULL REFERENCES phone_numbers(id),
  enabled boolean NOT NULL DEFAULT false,
  PRIMARY KEY (source_key_id, destination)
);
-- Populate bindings only from an independently reviewed source-key/number map.
-- Unbound inbound events remain quarantined, not assigned to the first tenant.
