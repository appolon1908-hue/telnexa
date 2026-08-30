"""Base state and event helpers for the Telnexa SMS provider service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from .sms_provider_contracts import canonical_json, sha256
from .sms_provider_transport import JasminTransport
from .sms_provider_models import (
    SmsCallbackOutbox,
    SmsOperation,
    SmsProviderEvent,
    SmsReservation,
)


@dataclass(slots=True)
class SmsProviderServiceBase:
    session_factory: sessionmaker[Session]
    transport: JasminTransport
    sell_price_per_segment: Decimal = Decimal("0.050000")
    currency: str = "USD"
    maximum_reconciliation_attempts: int = 3

    def _operation_json(self, operation: SmsOperation, *, replay: bool = False) -> dict[str, Any]:
        reservation: SmsReservation | None
        with self.session_factory() as session:
            reservation = session.scalar(
                select(SmsReservation).where(SmsReservation.operation_id == operation.id)
            )
        return {
            "operation_id": operation.id,
            "message_id": operation.middleware_message_id,
            "tenant_id": operation.tenant_id,
            "state": operation.state,
            "provider": operation.provider,
            "provider_reference": operation.provider_reference,
            "provider_status": operation.provider_status,
            "submission_attempts": operation.submission_attempts,
            "reconciliation_attempts": operation.reconciliation_attempts,
            "encoding": operation.encoding,
            "characters": operation.characters,
            "segments": operation.segments,
            "billing": {
                "reservation_id": reservation.id if reservation else None,
                "state": reservation.state if reservation else None,
                "amount": str(reservation.amount) if reservation else None,
                "currency": reservation.currency if reservation else None,
            },
            "last_error": operation.last_error,
            "replay": replay,
            "live_submission": False,
        }

    def _event(
        self,
        session: Session,
        operation: SmsOperation | None,
        *,
        event_id: str,
        event_type: str,
        canonical_status: str,
        provider_status: str | None,
        payload: dict[str, Any],
        occurred_at: datetime,
        ignored_transition: bool = False,
    ) -> SmsProviderEvent:
        event_payload = {
            **payload,
            "message_id": (
                operation.middleware_message_id if operation else payload.get("message_id")
            ),
            "provider_reference": (
                operation.provider_reference if operation else payload.get("provider_reference")
            ),
            "status": canonical_status,
            "provider_status": provider_status,
        }
        row = SmsProviderEvent(
            tenant_id=operation.tenant_id if operation else str(payload["tenant_id"]),
            operation_id=operation.id if operation else None,
            external_event_id=event_id,
            event_type=event_type,
            canonical_status=canonical_status,
            provider_status=provider_status,
            payload_sha256=sha256(canonical_json(event_payload)),
            payload=event_payload,
            ignored_transition=ignored_transition,
            occurred_at=occurred_at,
        )
        session.add(row)
        session.flush()
        session.add(
            SmsCallbackOutbox(
                tenant_id=row.tenant_id,
                event_id=event_id,
                event_type=event_type,
                payload={
                    "event_id": event_id,
                    "event_type": event_type,
                    "event_version": "1.0",
                    "timestamp": occurred_at.isoformat(),
                    "tenant_id": row.tenant_id,
                    "correlation_id": (
                        operation.correlation_id
                        if operation
                        else str(payload.get("correlation_id") or event_id)
                    ),
                    "idempotency_key": event_id,
                    "source_service": "telnexa-sms",
                    "payload": event_payload,
                    "metadata": {"provider": "jasmin"},
                },
            )
        )
        return row
