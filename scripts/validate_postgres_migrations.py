"""Fail closed unless production PostgreSQL migration invariants are present."""

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
        encrypted = CommandIdempotency(
            tenant_id="ci-tenant",
            caller_identity="ci-caller",
            resource="smpp_accounts",
            action="create",
            api_version="v1",
            idempotency_key="ci-encrypted-result",
            semantic_sha256="a" * 64,
            status_code=201,
            resource_id="ci-resource",
            response_json=None,
            response_ciphertext="ci-encrypted-placeholder",
        )
        session.add(encrypted)
        session.commit()
        session.refresh(encrypted)
        if encrypted.response_json is not None or not encrypted.response_ciphertext:
            raise SystemExit("encrypted response did not round-trip without JSON null")

        invalid = CommandIdempotency(
            tenant_id="ci-tenant",
            caller_identity="ci-caller",
            resource="smpp_accounts",
            action="create",
            api_version="v1",
            idempotency_key="ci-invalid-result",
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
    print("TELNEXA_POSTGRES_MIGRATIONS=PASS")


if __name__ == "__main__":
    main()
