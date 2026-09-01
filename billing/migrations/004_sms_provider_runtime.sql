-- Step 4 durable Telnexa SMS provider runtime.
-- Source only: applying this migration requires a separately reviewed deployment.

BEGIN;

CREATE TABLE IF NOT EXISTS sms_provider_operations (
    id varchar(36) PRIMARY KEY,
    tenant_id varchar(128) NOT NULL,
    middleware_message_id varchar(36) NOT NULL,
    command_id varchar(36) NOT NULL,
    idempotency_key varchar(180) NOT NULL,
    request_sha256 varchar(64) NOT NULL,
    correlation_id varchar(180) NOT NULL,
    requested_by varchar(300) NOT NULL,
    destination varchar(32) NOT NULL,
    sender varchar(20) NOT NULL,
    content_sha256 varchar(64) NOT NULL,
    encoding varchar(10) NOT NULL CHECK (encoding IN ('GSM-7', 'UCS-2')),
    characters integer NOT NULL CHECK (characters > 0),
    segments integer NOT NULL CHECK (segments > 0),
    category varchar(20) NOT NULL CHECK (category IN ('transactional', 'service', 'marketing')),
    client_reference varchar(120) NOT NULL,
    billing_account_id varchar(128),
    campaign_id varchar(128),
    state varchar(32) NOT NULL DEFAULT 'reserved',
    provider varchar(80) NOT NULL DEFAULT 'jasmin',
    provider_reference varchar(160),
    provider_status varchar(80),
    submission_attempts integer NOT NULL DEFAULT 0
        CHECK (submission_attempts >= 0 AND submission_attempts <= 1),
    reconciliation_attempts integer NOT NULL DEFAULT 0 CHECK (reconciliation_attempts >= 0),
    last_error varchar(500),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sms_provider_idempotency UNIQUE (tenant_id, idempotency_key),
    CONSTRAINT uq_sms_provider_message UNIQUE (tenant_id, middleware_message_id)
);

CREATE TABLE IF NOT EXISTS sms_provider_reservations (
    id varchar(36) PRIMARY KEY,
    tenant_id varchar(128) NOT NULL,
    operation_id varchar(36) NOT NULL UNIQUE
        REFERENCES sms_provider_operations(id) ON DELETE RESTRICT,
    amount numeric(18, 6) NOT NULL CHECK (amount >= 0),
    currency varchar(3) NOT NULL DEFAULT 'USD',
    state varchar(20) NOT NULL DEFAULT 'reserved'
        CHECK (state IN ('reserved', 'committed', 'released')),
    reason varchar(160),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sms_provider_events (
    id varchar(36) PRIMARY KEY,
    tenant_id varchar(128) NOT NULL,
    operation_id varchar(36) REFERENCES sms_provider_operations(id) ON DELETE RESTRICT,
    external_event_id varchar(256) NOT NULL,
    event_type varchar(120) NOT NULL,
    canonical_status varchar(32) NOT NULL,
    provider_status varchar(80),
    payload_sha256 varchar(64) NOT NULL,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    ignored_transition boolean NOT NULL DEFAULT false,
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sms_provider_event UNIQUE (tenant_id, external_event_id)
);

CREATE TABLE IF NOT EXISTS sms_provider_inbound (
    id varchar(36) PRIMARY KEY,
    tenant_id varchar(128) NOT NULL,
    provider varchar(80) NOT NULL DEFAULT 'jasmin',
    provider_message_id varchar(160) NOT NULL,
    sender varchar(32) NOT NULL,
    destination varchar(32) NOT NULL,
    content_sha256 varchar(64) NOT NULL,
    encoding varchar(10) NOT NULL CHECK (encoding IN ('GSM-7', 'UCS-2')),
    characters integer NOT NULL CHECK (characters > 0),
    segments integer NOT NULL CHECK (segments > 0),
    compliance_action varchar(20),
    occurred_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sms_provider_inbound UNIQUE (tenant_id, provider, provider_message_id)
);

CREATE TABLE IF NOT EXISTS sms_provider_opt_outs (
    id varchar(36) PRIMARY KEY,
    tenant_id varchar(128) NOT NULL,
    phone varchar(32) NOT NULL,
    scope_key varchar(160) NOT NULL DEFAULT 'tenant',
    reason varchar(160) NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sms_provider_opt_out UNIQUE (tenant_id, phone, scope_key)
);

CREATE TABLE IF NOT EXISTS sms_provider_callback_outbox (
    id varchar(36) PRIMARY KEY,
    tenant_id varchar(128) NOT NULL,
    event_id varchar(256) NOT NULL UNIQUE,
    event_type varchar(120) NOT NULL,
    payload jsonb NOT NULL,
    state varchar(20) NOT NULL DEFAULT 'pending',
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sms_provider_reconciliation_evidence (
    id varchar(36) PRIMARY KEY,
    tenant_id varchar(128) NOT NULL,
    operation_id varchar(36) NOT NULL
        REFERENCES sms_provider_operations(id) ON DELETE RESTRICT,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    outcome varchar(32) NOT NULL,
    provider_status varchar(80),
    provider_reference varchar(160),
    error varchar(500),
    created_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_sms_provider_reconciliation UNIQUE (operation_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS ix_sms_provider_operations_tenant_state
    ON sms_provider_operations (tenant_id, state, created_at);
CREATE INDEX IF NOT EXISTS ix_sms_provider_operations_provider_reference
    ON sms_provider_operations (tenant_id, provider_reference)
    WHERE provider_reference IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_sms_provider_reconciliation_backlog
    ON sms_provider_operations (updated_at)
    WHERE state IN ('reconciliation_required', 'manual_review');
CREATE INDEX IF NOT EXISTS ix_sms_provider_reserved_billing
    ON sms_provider_reservations (updated_at)
    WHERE state = 'reserved';
CREATE INDEX IF NOT EXISTS ix_sms_provider_callback_backlog
    ON sms_provider_callback_outbox (created_at)
    WHERE state IN ('pending', 'retrying');

CREATE OR REPLACE FUNCTION enforce_one_sms_provider_submission()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.submission_attempts < OLD.submission_attempts THEN
        RAISE EXCEPTION 'sms provider submission attempt cannot be reset';
    END IF;
    IF OLD.submission_attempts = 1 AND NEW.submission_attempts <> 1 THEN
        RAISE EXCEPTION 'sms provider submission already consumed';
    END IF;
    IF NEW.submission_attempts > 1 THEN
        RAISE EXCEPTION 'sms provider permits exactly one submission attempt';
    END IF;
    NEW.updated_at := CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_enforce_one_sms_provider_submission
    ON sms_provider_operations;
CREATE TRIGGER trg_enforce_one_sms_provider_submission
BEFORE UPDATE OF submission_attempts ON sms_provider_operations
FOR EACH ROW EXECUTE FUNCTION enforce_one_sms_provider_submission();

ALTER TABLE sms_provider_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sms_provider_reservations ENABLE ROW LEVEL SECURITY;
ALTER TABLE sms_provider_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE sms_provider_inbound ENABLE ROW LEVEL SECURITY;
ALTER TABLE sms_provider_opt_outs ENABLE ROW LEVEL SECURITY;
ALTER TABLE sms_provider_callback_outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE sms_provider_reconciliation_evidence ENABLE ROW LEVEL SECURITY;

DO $$
DECLARE
    table_name text;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'sms_provider_operations',
        'sms_provider_reservations',
        'sms_provider_events',
        'sms_provider_inbound',
        'sms_provider_opt_outs',
        'sms_provider_callback_outbox',
        'sms_provider_reconciliation_evidence'
    ]
    LOOP
        IF NOT EXISTS (
            SELECT 1
            FROM pg_policies
            WHERE schemaname = 'public'
              AND tablename = table_name
              AND policyname = table_name || '_tenant_isolation'
        ) THEN
            EXECUTE format(
                'CREATE POLICY %I ON %I USING (tenant_id = current_setting(''app.tenant_id'', true)) WITH CHECK (tenant_id = current_setting(''app.tenant_id'', true))',
                table_name || '_tenant_isolation',
                table_name
            );
        END IF;
    END LOOP;
END;
$$;

COMMIT;
