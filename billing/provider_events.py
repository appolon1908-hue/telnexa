import hashlib
import hmac
import json
import re
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from .engine import event
from .models import (
    ConsentRecord,
    Contact,
    CountryPolicy,
    InboundMessage,
    Message,
    Outbox,
    Tenant,
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
from .message_idempotency import lock_message_key
from .sms_integration import SmsInboundBinding


def _queue_webhooks(db, tenant_id, event_id, event_type):
    for hook in db.scalars(
        select(Webhook).where(Webhook.tenant_id == tenant_id, Webhook.enabled == True)
    ).all():
        if (event_type in hook.events or "*" in hook.events) and not db.scalar(
            select(WebhookDelivery).where(
                WebhookDelivery.webhook_id == hook.id, WebhookDelivery.event_id == event_id
            )
        ):
            db.add(WebhookDelivery(tenant_id=tenant_id, webhook_id=hook.id, event_id=event_id))


def signature(secret, method, path, timestamp, event_id, body, source_key_id=None):
    canonical = "\n".join(
        (
            "v2" if source_key_id is not None else "v1",
            method.upper(),
            "/" + "/".join(x for x in path.split("/") if x),
            timestamp,
            event_id,
            "telnexa",
            *([source_key_id] if source_key_id is not None else []),
            hashlib.sha256(body).hexdigest(),
        )
    ).encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def verify_signature(
    secret, method, path, timestamp, event_id, body, supplied, window=300, source_key_id=None
):
    try:
        fresh = abs(int(time.time()) - int(timestamp)) <= window
    except (TypeError, ValueError):
        return False
    if not secret or not isinstance(supplied, str) or not supplied.isascii():
        return False
    if source_key_id is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", source_key_id):
        return False
    if not isinstance(event_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", event_id):
        return False
    expected = "sha256=" + signature(secret, method, path, timestamp, event_id, body, source_key_id)
    return fresh and hmac.compare_digest(expected, supplied)


def _provider_event_identity(row):
    """Return a bounded identity for one authenticated provider event.

    Provider-local event identifiers are not globally unique. Hash the source,
    authenticated key identifier, and provider event identifier with explicit
    field boundaries so every downstream idempotency authority stays both
    collision-resistant and within its database column limit.
    """

    canonical = "\0".join((row.source, row.source_key_id, row.event_id)).encode()
    return hashlib.sha256(canonical).hexdigest()


def parse_payload(body):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate_provider_field")
            result[key] = value
        return result

    try:

        def invalid_constant(value):
            raise ValueError("invalid_provider_number")

        payload = json.loads(body, object_pairs_hook=unique_object, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("invalid_provider_payload") from exc
    if not isinstance(payload, dict) or set(payload) != {"event", "data"}:
        raise ValueError("invalid_provider_envelope")
    data = payload["data"]
    if not isinstance(data, dict) or not 1 <= len(data) <= 40:
        raise ValueError("invalid_provider_data")
    if any(
        not isinstance(key, str)
        or len(key) > 80
        or not isinstance(value, (str, int, float, type(None)))
        or len(str(value)) > 5000
        for key, value in data.items()
    ):
        raise ValueError("invalid_provider_fields")
    if not isinstance(payload["event"], str) or payload["event"] not in {
        "dlr",
        "failed",
        "inbound",
    }:
        raise ValueError("unsupported_provider_event")
    identifier = data.get("id") or data.get("messageid")
    if not isinstance(identifier, str) or not 1 <= len(identifier) <= 100:
        raise ValueError("provider_message_id_required")
    if payload["event"] == "inbound":
        for field, alias in (("from", "sender"), ("to", "destination")):
            value = data.get(field) or data.get(alias)
            if not isinstance(value, str) or not re.fullmatch(r"\+[1-9][0-9]{5,18}", value):
                raise ValueError("invalid_inbound_address")
        if not isinstance(data.get("content"), str):
            raise ValueError("invalid_inbound_content")
    return payload


def ingest(db, source_key_id, event_id, body, payload):
    # Serialize each authenticated source/event before read-then-insert; distinct
    # provider events remain independent, including reused provider-local IDs.
    lock_message_key(db, "provider:" + source_key_id, event_id)
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
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_provider_timestamp") from exc
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    if occurred_at.timestamp() > time.time() + 300:
        raise ValueError("provider_timestamp_in_future")
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
        .with_for_update(of=Message)
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
    provider_event_identity = _provider_event_identity(row)
    changed = transition(
        db,
        message,
        status,
        f"provider:{provider_event_identity}",
        evidence={"provider_status": raw, "source_key_id": row.source_key_id},
        occurred_at=row.occurred_at,
    )
    if changed and status != "unknown":
        event(
            db,
            message.tenant_id,
            f"sms.{status}",
            f"sms:{status}:{provider_event_identity}",
            message.correlation_id,
            {
                "message_id": message.id,
                "status": status,
                "provider_message_id": message.provider_message_id,
                "provider_event_id": provider_event_identity,
                "provider_status": raw,
                "segments": message.segments,
                "message_idempotency_key": message.idempotency_key,
            },
        )
        stored_event = db.scalar(
            select(Outbox).where(
                Outbox.tenant_id == message.tenant_id,
                Outbox.idempotency_key == f"sms:{status}:{provider_event_identity}",
            )
        )
        if stored_event:
            _queue_webhooks(db, message.tenant_id, stored_event.id, f"sms.{status}")
    row.state = "processed"


def _mo(db, row, data):
    lock_message_key(db, "inbound:" + row.source_key_id, row.provider_message_id or row.event_id)
    destination = data.get("to") or data.get("destination")
    number = db.scalar(
        select(PhoneNumber)
        .join(SmsInboundBinding, SmsInboundBinding.number_id == PhoneNumber.id)
        .where(
            SmsInboundBinding.source_key_id == row.source_key_id,
            SmsInboundBinding.destination == destination,
            SmsInboundBinding.enabled == True,
            PhoneNumber.number == destination,
            PhoneNumber.status == "active",
        )
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
    # Synchronize STOP/consent changes with admission on the same tenant.
    db.scalar(select(Tenant).where(Tenant.id == number.tenant_id).with_for_update())
    row.tenant_id = number.tenant_id
    sender = data.get("from") or data.get("sender")
    content = data.get("content", "")
    provider_identity = "jasmin:" + hashlib.sha256(row.source_key_id.encode()).hexdigest()[:32]
    inbound = db.scalar(
        select(InboundMessage).where(
            InboundMessage.provider == provider_identity,
            InboundMessage.provider_message_id == row.provider_message_id,
        )
    )
    if not inbound:
        inbound = InboundMessage(
            tenant_id=number.tenant_id,
            provider=provider_identity,
            provider_message_id=row.provider_message_id or row.event_id,
            sender=sender,
            destination=destination,
            content=content,
            conversation_key=f"{number.tenant_id}:{sender}:{destination}",
        )
        db.add(inbound)
        db.flush()
    elif (
        inbound.tenant_id != number.tenant_id
        or inbound.sender != sender
        or inbound.destination != destination
        or inbound.content != content
    ):
        row.state = "quarantined"
        return
    else:
        # Same provider message with a new transport event ID is still a replay.
        row.state = "processed"
        return
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
        f"sms:mo:{_provider_event_identity(row)}",
        str(uuid.UUID(hex=_provider_event_identity(row)[:32])),
        {
            "inbound_message_id": inbound.id,
            "provider_message_id": inbound.provider_message_id,
            "provider_event_id": _provider_event_identity(row),
            "sender": inbound.sender,
            "destination": inbound.destination,
            "content": inbound.content,
            "compliance_action": {
                "sms.opted_out": "stop",
                "sms.help_requested": "help",
                "sms.opted_in": "start",
            }.get(event_type),
        },
    )
    stored_event = db.scalar(
        select(Outbox).where(
            Outbox.tenant_id == number.tenant_id,
            Outbox.idempotency_key == f"sms:mo:{_provider_event_identity(row)}",
        )
    )
    _queue_webhooks(db, number.tenant_id, stored_event.id, event_type)
    row.state = "processed"


def process_event(db, row):
    if row.state == "processed":
        return
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
