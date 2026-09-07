from datetime import datetime, timezone

from sqlalchemy import select

from .models import MessageEvent

TERMINAL = {"delivered", "rejected", "failed", "expired", "undeliverable", "cancelled"}
TRANSITIONS = {
    "accepted": {"queued", "cancelled"},
    "queued": {"dispatching", "rejected", "cancelled"},
    "dispatching": {"submitted", "retry_wait", "submission_unknown", "rejected", "failed"},
    "retry_wait": {"dispatching", "failed", "cancelled"},
    "submission_unknown": {"submitted", "sent", "delivered", "failed", "expired", "undeliverable"},
    "submitted": {"sent", "delivered", "failed", "expired", "undeliverable"},
    "sent": {"delivered", "failed", "expired", "undeliverable"},
}


def transition(
    db, message, status, external_event_id, event_type=None, evidence=None, occurred_at=None
):
    prior = db.scalar(
        select(MessageEvent).where(
            MessageEvent.tenant_id == message.tenant_id,
            MessageEvent.external_event_id == external_event_id,
        )
    )
    if prior:
        return False
    allowed = status == message.status or status in TRANSITIONS.get(message.status, set())
    db.add(
        MessageEvent(
            tenant_id=message.tenant_id,
            message_id=message.id,
            external_event_id=external_event_id,
            type=event_type or f"sms.{status}",
            status=status,
            provider_response=evidence or {},
            occurred_at=occurred_at or datetime.now(timezone.utc),
        )
    )
    if allowed and status != message.status:
        message.status = status
        message.updated_at = datetime.now(timezone.utc)
        if status == "submitted":
            message.submitted_at = message.updated_at
        if status == "delivered":
            message.delivered_at = message.updated_at
        if status in TERMINAL:
            message.terminal_at = message.updated_at
    return allowed
