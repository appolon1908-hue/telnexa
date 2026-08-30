"""Serialized, read-back-only recovery for uncertain SMS provider outcomes."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from .sms_provider_contracts import ProviderNotFound, utcnow
from .sms_provider_models import (
    SmsOperation,
    SmsReconciliationEvidence,
    SmsReservation,
)
from .sms_provider_transport import ReadbackResult


class SmsProviderReconciliationMixin:
    """Reconcile uncertain provider state without ever resubmitting an SMS.

    The operation row is locked for the complete read-back transaction. That
    makes reconciliation attempt numbers and evidence inserts serial across
    workers. It also gives operations stranded in ``submitting`` after a process
    interruption an authoritative read-back recovery path.
    """

    def reconcile(self, tenant_id: str, operation_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            operation = session.scalar(
                select(SmsOperation)
                .where(
                    SmsOperation.id == operation_id,
                    SmsOperation.tenant_id == tenant_id,
                )
                .with_for_update()
            )
            if not operation:
                raise ProviderNotFound("SMS provider operation was not found")

            recoverable_states = {
                "submitting",
                "reconciliation_required",
                "manual_review",
            }
            if operation.state not in recoverable_states:
                session.commit()
                return self._operation_json(operation)

            if operation.reconciliation_attempts >= self.maximum_reconciliation_attempts:
                operation.state = "manual_review"
                operation.last_error = operation.last_error or (
                    "maximum authoritative read-back attempts exhausted"
                )
                session.commit()
                return self._operation_json(operation)

            recovered_from_submitting = operation.state == "submitting"
            operation.reconciliation_attempts += 1
            attempt = operation.reconciliation_attempts
            if recovered_from_submitting:
                operation.last_error = (
                    "submission result was not durably recorded; authoritative read-back required"
                )

            try:
                result = self.transport.readback(operation)
            except Exception as error:  # read-back may retry; submission may not
                result = ReadbackResult("unknown", error=type(error).__name__)

            reservation = session.scalar(
                select(SmsReservation).where(
                    SmsReservation.operation_id == operation.id
                )
            )
            assert reservation is not None

            effective_reference = (
                result.provider_reference or operation.provider_reference
            )
            operation.provider_reference = effective_reference
            operation.provider_status = result.provider_status or operation.provider_status
            operation.last_error = result.error

            reference_required = (
                result.outcome in {"accepted", "delivered"}
                and not effective_reference
            )
            if reference_required:
                operation.last_error = (
                    result.error
                    or "authoritative read-back returned acceptance without a durable provider reference"
                )
                reservation.reason = "authoritative_readback_missing_provider_reference"
                if attempt >= self.maximum_reconciliation_attempts:
                    operation.state = "manual_review"
                else:
                    operation.state = "reconciliation_required"
            elif result.outcome == "delivered":
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
            elif attempt >= self.maximum_reconciliation_attempts:
                operation.state = "manual_review"
                operation.last_error = operation.last_error or (
                    "authoritative provider state remains unknown"
                )
            else:
                operation.state = "reconciliation_required"
                reservation.reason = "authoritative_readback_inconclusive"

            session.add(
                SmsReconciliationEvidence(
                    tenant_id=tenant_id,
                    operation_id=operation.id,
                    attempt_number=attempt,
                    outcome=result.outcome,
                    provider_status=result.provider_status,
                    provider_reference=result.provider_reference,
                    error=operation.last_error,
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
                    "recovered_from_submitting": recovered_from_submitting,
                    "durable_provider_reference": bool(effective_reference),
                },
                occurred_at=utcnow(),
            )
            session.commit()
            return self._operation_json(operation)


__all__ = ["SmsProviderReconciliationMixin"]
