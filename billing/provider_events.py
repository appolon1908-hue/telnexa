import hashlib
import hmac
import time
from datetime import datetime, timezone

from sqlalchemy import select

from .engine import event
from .models import (
    ConsentRecord,
    Contact,
    CountryPolicy,
    InboundMessage,
    Message,
    PhoneNumber,
    Provider,
    Route,
    SmsProviderEventAttempt,
    SmsProviderEventInbox,
    SmsReconciliationCase,
    SmsRouteDecision,
    Webhook,
    WebhookDelivery,
)
from .state_machine import transition


def _queue_webhooks(db, tenant_id, event_id, event_type):
    for hook in db.scalars(
        select(Webhook).where(Webhook.tenant_id == tenant_id, Webhook.enabled == True)
    ).all():
        if event_type in hook.events and not db.scalar(
            select(WebhookDelivery).where(
                WebhookDelivery.webhook_id == hook.id, WebhookDelivery.event_id == event_id
            )
        ):
            db.add(WebhookDelivery(tenant_id=tenant_id, webhook_id=hook.id, event_id=event_id))


def signature(secret, method, path, timestamp, event_id, body):
    canonical = "\n".join(
        (
            "v1",
            method.upper(),
            "/" + "/".join(x for x in path.split("/") if x),
            timestamp,
            event_id,
            "telnexa",
            hashlib.sha256(body).hexdigest(),
        )
    ).encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def verify_signature(secret, method, path, timestamp, event_id, body, supplied, window=300):
    try:
        fresh = abs(int(time.time()) - int(timestamp)) <= window
    except (TypeError, ValueError):
        return False
    expected = "sha256=" + signature(secret, method, path, timestamp, event_id, body)
    return fresh and bool(event_id) and hmac.compare_digest(expected, supplied or "")


def ingest(db, source_key_id, event_id, body, payload):
    prior = db.scalar(
        select(SmsProviderEventInbox).where(
            SmsProviderEventInbox.source_key_id == source_key_id,
            SmsProviderEventInbox.event_id == event_id,
        )
    )
    if prior:
        if prior.payload_hash != hashlib.sha256(body).hexdigest():
            raise ValueError("provider_event_replay_payload_mismatch")
        return prior, True
    data = payload.get("data", {})
    kind = {"dlr": "DLR", "failed": "FAILURE", "inbound": "MO"}.get(payload.get("event"))
    if not kind:
        raise ValueError("unsupported_provider_event")
    occurred = data.get("occurred_at") or data.get("timestamp")
    try:
        occurred_at = (
            datetime.fromisoformat(str(occurred).replace("Z", "+00:00"))
            if occurred
            else datetime.now(timezone.utc)
        )
    except ValueError:
        occurred_at = datetime.now(timezone.utc)
    row = SmsProviderEventInbox(
        source="jasmin",
        source_key_id=source_key_id,
        event_id=event_id,
        event_type=kind,
        provider_message_id=data.get("id") or data.get("messageid"),
        payload_hash=hashlib.sha256(body).hexdigest(),
        normalized_payload=payload,
        occurred_at=occurred_at,
    )
    db.add(row)
    db.flush()
    return row, False


def _dlr(db, row, data):
    matches = db.scalars(
        select(Message)
        .join(SmsRouteDecision, SmsRouteDecision.id == Message.route_decision_id)
        .join(Provider, Provider.id == SmsRouteDecision.selected_provider_id)
        .where(
            Message.provider_message_id == row.provider_message_id,
            Provider.dlr_source_key_id == row.source_key_id,
        )
    ).all()
    if len(matches) != 1:
        db.add(
            SmsReconciliationCase(
                case_type=(
                    "ambiguous_provider_event" if len(matches) > 1 else "unmatched_provider_event"
                ),
                reference_id=row.id,
                evidence={
                    "provider_message_id": row.provider_message_id,
                    "source_key_id": row.source_key_id,
                    "match_count": len(matches),
                },
            )
        )
        row.state = "quarantined"
        return
    message = matches[0]
    row.message_id, row.tenant_id = message.id, message.tenant_id
    raw = str(data.get("message_status") or data.get("status") or "unknown").upper()
    status = {
        "DELIVRD": "delivered",
        "ACCEPTD": "submitted",
        "ENROUTE": "sent",
        "SENT": "sent",
        "UNDELIV": "undeliverable",
        "EXPIRED": "expired",
        "REJECTD": "failed",
        "FAILED": "failed",
    }.get(raw, "unknown")
    changed = transition(
        db,
        message,
        status,
        f"provider:{row.source}:{row.event_id}",
        evidence={"provider_status": raw},
        occurred_at=row.occurred_at,
    )
    if changed and status != "unknown":
        event(
            db,
            message.tenant_id,
            f"sms.{status}",
            f"sms:{status}:{row.event_id}",
            message.correlation_id,
            {"message_id": message.id, "status": status},
        )
        _queue_webhooks(db, message.tenant_id, row.id, f"sms.{status}")
    row.state = "processed"


def _mo(db, row, data):
    destination = data.get("to") or data.get("destination")
    number = db.scalar(
        select(PhoneNumber).where(PhoneNumber.number == destination, PhoneNumber.status == "active")
    )
    if not number:
        db.add(
            SmsReconciliationCase(
                case_type="unmatched_provider_event",
                reference_id=row.id,
                evidence={"event_type": "MO"},
            )
        )
        row.state = "quarantined"
        return
    row.tenant_id = number.tenant_id
    sender = data.get("from") or data.get("sender")
    content = data.get("content", "")
    inbound = db.scalar(
        select(InboundMessage).where(
            InboundMessage.provider == "jasmin",
            InboundMessage.provider_message_id == row.provider_message_id,
        )
    )
    if not inbound:
        inbound = InboundMessage(
            tenant_id=number.tenant_id,
            provider="jasmin",
            provider_message_id=row.provider_message_id or row.event_id,
            sender=sender,
            destination=destination,
            content=content,
            conversation_key=f"{number.tenant_id}:{sender}:{destination}",
        )
        db.add(inbound)
    keyword = content.strip().upper()
    event_type = "sms.inbound.received"
    contact = db.scalar(
        select(Contact).where(Contact.tenant_id == number.tenant_id, Contact.phone == sender)
    )
    latest_consent = db.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.tenant_id == number.tenant_id, ConsentRecord.phone == sender)
        .order_by(ConsentRecord.occurred_at.desc())
        .limit(1)
    )
    event_time = row.occurred_at
    latest_time = latest_consent.occurred_at if latest_consent else None
    if event_time and event_time.tzinfo is None:
        event_time = event_time.replace(tzinfo=timezone.utc)
    if latest_time and latest_time.tzinfo is None:
        latest_time = latest_time.replace(tzinfo=timezone.utc)
    event_is_current = not latest_time or event_time >= latest_time
    if keyword in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}:
        if not contact and event_is_current:
            contact = Contact(tenant_id=number.tenant_id, phone=sender)
            db.add(contact)
        if event_is_current and contact and contact.consent_status != "opted_out":
            contact.consent_status = "opted_out"
            contact.opted_out_at = row.occurred_at
            contact.suppression_reason = "inbound_stop"
            db.add(
                ConsentRecord(
                    tenant_id=number.tenant_id,
                    phone=sender,
                    action="opt_out",
                    source="inbound_sms",
                    occurred_at=row.occurred_at,
                    metadata_json={
                        "event_id": row.event_id,
                        "occurred_at": row.occurred_at.isoformat(),
                    },
                )
            )
            event_type = "sms.opted_out"
    elif keyword == "HELP":
        event_type = "sms.help_requested"
    elif keyword in {"START", "UNSTOP"}:
        routes = [
            r
            for r in db.scalars(select(Route).where(Route.enabled == True)).all()
            if r.tenant_id in (None, number.tenant_id) and destination.startswith(r.prefix)
        ]
        routes.sort(
            key=lambda r: (r.tenant_id == number.tenant_id, len(r.prefix), r.priority), reverse=True
        )
        policy = (
            db.scalar(
                select(CountryPolicy).where(
                    CountryPolicy.country == routes[0].country,
                    CountryPolicy.category == "marketing",
                    CountryPolicy.enabled == True,
                )
            )
            if routes
            else None
        )
        allowed = bool(policy and policy.config.get("allow_inbound_reopt_in") is True)
        if (
            allowed
            and event_is_current
            and (
                not contact
                or contact.consent_status != "opted_in"
                or contact.opted_out_at is not None
            )
        ):
            if not contact:
                contact = Contact(tenant_id=number.tenant_id, phone=sender)
                db.add(contact)
            contact.consent_status = "opted_in"
            contact.consent_at = row.occurred_at
            contact.consent_source = "inbound_sms"
            contact.opted_out_at = None
            contact.suppression_reason = None
            db.add(
                ConsentRecord(
                    tenant_id=number.tenant_id,
                    phone=sender,
                    action="opt_in",
                    source="inbound_sms",
                    occurred_at=row.occurred_at,
                    metadata_json={
                        "event_id": row.event_id,
                        "occurred_at": row.occurred_at.isoformat(),
                        "keyword": keyword,
                    },
                )
            )
            event_type = "sms.opted_in"
    event(
        db,
        number.tenant_id,
        event_type,
        f"sms:mo:{row.event_id}",
        row.event_id,
        {"inbound_message_id": inbound.id},
    )
    _queue_webhooks(db, number.tenant_id, row.id, event_type)
    row.state = "processed"


def process_event(db, row):
    row.attempts += 1
    attempt = SmsProviderEventAttempt(
        inbox_id=row.id, tenant_id=row.tenant_id, attempt_number=row.attempts, outcome="processing"
    )
    db.add(attempt)
    data = row.normalized_payload.get("data", {})
    if row.event_type in {"DLR", "FAILURE"}:
        _dlr(db, row, data)
    else:
        _mo(db, row, data)
    attempt.tenant_id = row.tenant_id
    attempt.outcome = row.state
    attempt.completed_at = datetime.now(timezone.utc)
