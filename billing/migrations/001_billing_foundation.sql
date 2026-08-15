-- SQLAlchemy creates the portable schema. PostgreSQL enforces ledger immutability at the database boundary.
CREATE OR REPLACE FUNCTION reject_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'ledger entries are immutable; create a reversal entry'; END $$;
DROP TRIGGER IF EXISTS ledger_entries_immutable ON ledger_entries;
CREATE TRIGGER ledger_entries_immutable BEFORE UPDATE OR DELETE ON ledger_entries FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

DROP TRIGGER IF EXISTS audit_log_immutable ON audit_log;
CREATE TRIGGER audit_log_immutable BEFORE UPDATE OR DELETE ON audit_log FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- Defense in depth. The application role sets app.tenant_id per request in a
-- hardened deployment; owners/migrations retain BYPASSRLS only for operations.
DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['wallets','ledger_entries','balance_reservations','messages','usage_records','invoices','api_keys','audit_log','billing_outbox','team_members','senders','contacts','templates','campaigns','webhooks','webhook_deliveries','message_events','inbound_messages','smpp_credentials'] LOOP
    IF to_regclass(t) IS NOT NULL THEN
      EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
      EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
      EXECUTE format('CREATE POLICY tenant_isolation ON %I USING (tenant_id = nullif(current_setting(''app.tenant_id'', true), '''')) WITH CHECK (tenant_id = nullif(current_setting(''app.tenant_id'', true), ''''))', t);
    END IF;
  END LOOP;
END $$;
