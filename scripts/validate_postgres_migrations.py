"""Fail closed unless production PostgreSQL migration invariants are present."""

import uuid

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from billing.db import SessionLocal, engine
from billing.models import CommandIdempotency


def scalar(sql: str):
    with engine.connect() as connection:
        return connection.scalar(text(sql))


def main() -> None:
    if engine.dialect.name != "postgresql":
        raise SystemExit("BILLING_DATABASE_URL must select PostgreSQL")
    constraints = scalar(
        """
        SELECT count(*)
          FROM pg_constraint
         WHERE conname IN (
           'uq_command_idempotency_identity',
           'ck_command_idempotency_response'
         )
        """
    )
    if constraints != 2:
        raise SystemExit(f"command idempotency constraints missing: {constraints}/2")
    smpp_enablement_gate = scalar(
        """
        SELECT count(*)
          FROM pg_constraint
         WHERE conname = 'ck_smpp_credentials_runtime_provisioned_before_enable'
           AND conrelid = 'smpp_credentials'::regclass
        """
    )
    if smpp_enablement_gate != 1:
        raise SystemExit(f"SMPP runtime enablement gate missing: {smpp_enablement_gate}/1")
    production_tables = scalar(
        """
        SELECT count(*)
          FROM pg_class
         WHERE relname IN (
           'sms_production_authorizations',
           'sms_production_authorization_revocations',
           'sms_delivery_policies',
           'sms_system_controls'
         )
           AND relkind = 'r'
        """
    )
    if production_tables != 4:
        raise SystemExit(f"SMS production policy tables missing: {production_tables}/4")
    immutable_triggers = scalar(
        """
        SELECT count(*)
          FROM pg_trigger
         WHERE tgname IN (
           'sms_production_authorizations_immutable',
           'sms_production_authorization_revocations_immutable'
         )
           AND NOT tgisinternal
        """
    )
    if immutable_triggers != 2:
        raise SystemExit(
            f"SMS authorization immutability triggers missing: {immutable_triggers}/2"
        )
    production_policy_link = scalar(
        """
        SELECT count(*)
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = 'sms_dispatch_jobs'
           AND column_name = 'production_policy_id'
        """
    )
    if production_policy_link != 1:
        raise SystemExit("SMS dispatch production policy identity is missing")
    if (
        scalar("SELECT relrowsecurity FROM pg_class WHERE relname = 'command_idempotency'")
        is not True
    ):
        raise SystemExit("command_idempotency row-level security is disabled")
    policies = scalar(
        """
        SELECT count(*)
          FROM pg_policies
         WHERE tablename = 'command_idempotency'
           AND policyname = 'tenant_isolation'
        """
    )
    if policies != 1:
        raise SystemExit(f"command idempotency tenant policy missing: {policies}/1")
    with SessionLocal() as session:
        run_id = uuid.uuid4().hex
        encrypted = CommandIdempotency(
            tenant_id="ci-tenant",
            caller_identity="ci-caller",
            resource="smpp_accounts",
            action="create",
            api_version="v1",
            idempotency_key="ci-encrypted-result-" + run_id,
            semantic_sha256="a" * 64,
            status_code=201,
            resource_id="ci-resource",
            response_json=None,
            response_ciphertext="ci-encrypted-placeholder",
        )
        session.add(encrypted)
        session.commit()
        session.refresh(encrypted)
        encrypted_id = encrypted.id
        if encrypted.response_json is not None or not encrypted.response_ciphertext:
            raise SystemExit("encrypted response did not round-trip without JSON null")

        invalid = CommandIdempotency(
            tenant_id="ci-tenant",
            caller_identity="ci-caller",
            resource="smpp_accounts",
            action="create",
            api_version="v1",
            idempotency_key="ci-invalid-result-" + run_id,
            semantic_sha256="b" * 64,
            status_code=201,
            resource_id="ci-resource-2",
            response_json=None,
            response_ciphertext=None,
        )
        session.add(invalid)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise SystemExit("command idempotency response constraint accepted an empty result")
        persisted = session.get(CommandIdempotency, encrypted_id)
        if persisted is not None:
            session.delete(persisted)
            session.commit()
    print("TELNEXA_POSTGRES_MIGRATIONS=PASS")


if __name__ == "__main__":
    main()
