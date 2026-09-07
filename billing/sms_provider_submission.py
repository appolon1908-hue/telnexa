"""Submission, exact-idempotency, and no-resubmission reconciliation logic."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .sms_provider_contracts import (
    IdempotencyConflict,
    ProviderDisabled,
    ProviderNotFound,
    SmsCommand,
    canonical_json,
    money,
    sha256,
    utcnow,
)
from .sms_provider_models import (
    SmsOperation,
    SmsReconciliationEvidence,
    SmsReservation,
)
from .sms_provider_transport import (
    DisabledJasminTransport,
    ReadbackResult,
    SubmissionResult,
)


class SmsProviderSubmissionMixin:
    def submit(self, command: SmsCommand) -> dict[str, Any]:
        command_dump = command.model_dump(mode="json")
        request_digest = sha256(canonical_json(command_dump))
        payload = command.payload
        with self.session_factory() as session:
            existing = session.scalar(
                select(SmsOperation).where(
                    SmsOperation.tenant_id == command.tenant_id,
                    SmsOperation.idempotency_key == command.idempotency_key,
                )
            )
            if existing:
                if existing.request_sha256 != request_digest:
                    raise IdempotencyConflict(
                        "Idempotency-Key was reused with different SMS content"
                    )
                return self._operation_json(existing, replay=True)

            if isinstance(self.transport, DisabledJasminTransport):
                raise ProviderDisabled("Jasmin provider transport is disabled")

            operation = SmsOperation(
                tenant_id=command.tenant_id,
                middleware_message_id=str(payload.message_id),
                command_id=str(command.command_id),
                idempotency_key=command.idempotency_key,
                request_sha256=request_digest,
                correlation_id=command.correlation_id,
                requested_by=command.requested_by,
                destination=payload.destination,
                sender=payload.sender,
                content_sha256=sha256(payload.content),
                encoding=payload.encoding,
                characters=payload.characters,
                segments=payload.segments,
                category=payload.category,
                client_reference=payload.client_reference,
                billing_account_id=payload.billing_account_id,
                campaign_id=payload.campaign_id,
                state="reserved",
                provider=self.transport.name,
            )
            session.add(operation)
            session.flush()
            session.add(
                SmsReservation(
                    tenant_id=command.tenant_id,
                    operation_id=operation.id,
                    amount=money(self.sell_price_per_segment * payload.segments),
                    currency=self.currency,
                    state="reserved",
                )
            )
            operation.submission_attempts = 1
            operation.state = "submitting"
            session.commit()
            operation_id = operation.id

        try:
            result = self.transport.submit(command)
        except Exception as error:  # defensive: ambiguous network/library failure
            result = SubmissionResult("unknown", error=type(error).__name__)

        with self.session_factory() as session:
            operation = session.get(SmsOperation, operation_id)
            assert operation is not None
            reservation = session.scalar(
                select(SmsReservation).where(SmsReservation.operation_id == operation.id)
            )
            assert reservation is not None
            operation.provider_reference = result.provider_reference
            operation.provider_status = result.provider_status
            operation.last_error = result.error
            if result.outcome == "accepted":
                if not result.provider_reference:
                    operation.state = "reconciliation_required"
                    operation.last_error = "provider accepted without durable reference"
                else:
                    operation.state = "provider_accepted"
                    reservation.state = "committed"
                    reservation.reason = "provider_accepted"
            elif result.outcome == "rejected":
                operation.state = "failed"
                reservation.state = "released"
                reservation.reason = "provider_rejected_before_acceptance"
            else:
                operation.state = "reconciliation_required"
                reservation.reason = "provider_outcome_unknown"

            self._event(
                session,
                operation,
                event_id=f"submission:{operation.id}:1",
                event_type=(
                    "sms.message.failed.v1"
                    if operation.state == "failed"
                    else "sms.message.submitted.v1"
                ),
                canonical_status=operation.state,
                provider_status=operation.provider_status,
                payload={
                    "submission_attempts": operation.submission_attempts,
                    "reconciliation_required": operation.state == "reconciliation_required",
                },
                occurred_at=utcnow(),
            )
            session.commit()
            session.refresh(operation)
            return self._operation_json(operation)

    def get(self, tenant_id: str, operation_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            operation = session.scalar(
                select(SmsOperation).where(
                    SmsOperation.id == operation_id,
                    SmsOperation.tenant_id == tenant_id,
                )
            )
            if not operation:
                raise ProviderNotFound("SMS provider operation was not found")
            return self._operation_json(operation)

    def get_by_message(self, tenant_id: str, message_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            operation = session.scalar(
                select(SmsOperation).where(
                    SmsOperation.tenant_id == tenant_id,
                    SmsOperation.middleware_message_id == message_id,
                )
            )
            if not operation:
                raise ProviderNotFound("SMS provider operation was not found")
            return self._operation_json(operation)

    def reconcile(self, tenant_id: str, operation_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            operation = session.scalar(
                select(SmsOperation).where(
                    SmsOperation.id == operation_id,
                    SmsOperation.tenant_id == tenant_id,
                )
            )
            if not operation:
                raise ProviderNotFound("SMS provider operation was not found")
            if operation.state not in {"reconciliation_required", "manual_review"}:
                return self._operation_json(operation)
            if operation.reconciliation_attempts >= self.maximum_reconciliation_attempts:
                operation.state = "manual_review"
                session.commit()
                return self._operation_json(operation)
            operation.reconciliation_attempts += 1
            attempt = operation.reconciliation_attempts
            session.commit()

        try:
            result = self.transport.readback(operation)
        except Exception as error:  # defensive: read-back may be retried; submit may not
            result = ReadbackResult("unknown", error=type(error).__name__)

        with self.session_factory() as session:
            operation = session.get(SmsOperation, operation_id)
            assert operation is not None
            reservation = session.scalar(
                select(SmsReservation).where(SmsReservation.operation_id == operation.id)
            )
            assert reservation is not None
            operation.provider_reference = result.provider_reference or operation.provider_reference
            operation.provider_status = result.provider_status or operation.provider_status
            operation.last_error = result.error
            if result.outcome == "delivered":
                operation.state = "delivered"
                reservation.state = "committed"
                reservation.reason = "authoritative_readback_delivered"
            elif result.outcome == "accepted":
                operation.state = "provider_accepted"
                reservation.state = "committed"
                reservation.reason = "authoritative_readback_accepted"
            elif result.outcome == "failed":
                operation.state = "failed"
                reservation.state = "released"
                reservation.reason = "authoritative_readback_failed"
            elif operation.reconciliation_attempts >= self.maximum_reconciliation_attempts:
                operation.state = "manual_review"
            else:
                operation.state = "reconciliation_required"

            session.add(
                SmsReconciliationEvidence(
                    tenant_id=tenant_id,
                    operation_id=operation.id,
                    attempt_number=attempt,
                    outcome=result.outcome,
                    provider_status=result.provider_status,
                    provider_reference=result.provider_reference,
                    error=result.error,
                )
            )
            self._event(
                session,
                operation,
                event_id=f"reconciliation:{operation.id}:{attempt}",
                event_type="sms.message.reconciled.v1",
                canonical_status=operation.state,
                provider_status=operation.provider_status,
                payload={
                    "readback_attempt": attempt,
                    "submission_attempts": operation.submission_attempts,
                    "provider_resubmissions": 0,
                },
                occurred_at=utcnow(),
            )
            session.commit()
            return self._operation_json(operation)
