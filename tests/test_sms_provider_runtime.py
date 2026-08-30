from __future__ import annotations

import json
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from billing.sms_provider_runtime import (
    CallbackAuthenticationError,
    CallbackReplayConflict,
    DlrCallback,
    IdempotencyConflict,
    MoCallback,
    ReadbackResult,
    ScriptedJasminTransport,
    SmsCallbackOutbox,
    SmsCommand,
    SmsOperation,
    SmsProviderEvent,
    SmsProviderService,
    SmsReservation,
    SubmissionResult,
    canonical_json,
    create_session_factory,
    middleware_callback,
    segment_info,
    sha256,
    verify_callback,
)

TENANT = "tenant-stage4"
MIDDLEWARE_TOKEN = "m" * 48
CALLBACK_SECRET = "c" * 48


def command(*, key: str = "sms-idempotency-0001", content: str = "hello") -> SmsCommand:
    segments = segment_info(content)
    message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"codestra:{key}:message")
    return SmsCommand.model_validate(
        {
            "command_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"codestra:{key}:command")),
            "command_type": "sms.message.submit.v1",
            "command_version": "1.0",
            "target": "telnexa-sms",
            "tenant_id": TENANT,
            "requested_by": "middleware-stage4",
            "correlation_id": f"corr-{key}",
            "idempotency_key": key,
            "capability": "SMS_DELIVERY",
            "payload": {
                "message_id": str(message_id),
                "channel": "sms",
                "destination": "+18095550123",
                "sender": "CODESTRA",
                "content": content,
                "encoding": segments.encoding,
                "characters": segments.characters,
                "segments": segments.segments,
                "category": "transactional",
                "client_reference": str(message_id),
                "scheduled_at": None,
                "billing_account_id": "billing-stage4",
                "campaign_id": None,
            },
        }
    )


def service(tmp_path, transport: ScriptedJasminTransport) -> SmsProviderService:
    _, factory = create_session_factory(f"sqlite:///{tmp_path / 'sms.db'}")
    return SmsProviderService(
        session_factory=factory,
        transport=transport,
        sell_price_per_segment=Decimal("0.05"),
    )


def callback_headers(secret: str, event_id: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    import hashlib
    import hmac

    signature = hmac.new(
        secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
    ).hexdigest()
    return {
        "X-Telnexa-Timestamp": timestamp,
        "X-Telnexa-Event-Id": event_id,
        "X-Telnexa-Signature": "sha256=" + signature,
    }


def test_encoding_and_segment_boundaries() -> None:
    assert segment_info("a" * 160).segments == 1
    assert segment_info("a" * 161).segments == 2
    assert segment_info("€" * 80).segments == 1
    assert segment_info("€" * 81).segments == 2
    assert segment_info("🙂" * 35).segments == 1
    assert segment_info("🙂" * 36).segments == 2


def test_exact_idempotency_and_single_billing_reservation(tmp_path) -> None:
    transport = ScriptedJasminTransport(
        submissions=[SubmissionResult("accepted", "jasmin-ref-1", "accepted")]
    )
    provider = service(tmp_path, transport)
    request = command()

    first = provider.submit(request)
    replay = provider.submit(request)

    assert first["state"] == "provider_accepted"
    assert first["submission_attempts"] == 1
    assert first["billing"]["state"] == "committed"
    assert replay["replay"] is True
    assert transport.submit_calls == 1

    with provider.session_factory() as session:
        assert session.query(SmsOperation).count() == 1
        assert session.query(SmsReservation).count() == 1

    changed = command(content="changed payload")
    changed.idempotency_key = request.idempotency_key
    with pytest.raises(IdempotencyConflict):
        provider.submit(changed)


def test_unknown_outcome_uses_readback_and_never_resubmits(tmp_path) -> None:
    transport = ScriptedJasminTransport(
        submissions=[SubmissionResult("unknown", error="timeout_after_acceptance")],
        readbacks=[
            ReadbackResult("unknown", error="temporary_readback_failure"),
            ReadbackResult("unknown", error="temporary_readback_failure"),
            ReadbackResult("accepted", "jasmin-ref-unknown", "accepted"),
        ],
    )
    provider = service(tmp_path, transport)
    submitted = provider.submit(command(key="sms-idempotency-unknown"))

    assert submitted["state"] == "reconciliation_required"
    assert submitted["submission_attempts"] == 1
    assert submitted["billing"]["state"] == "reserved"

    first = provider.reconcile(TENANT, submitted["operation_id"])
    second = provider.reconcile(TENANT, submitted["operation_id"])
    third = provider.reconcile(TENANT, submitted["operation_id"])

    assert first["state"] == "reconciliation_required"
    assert second["state"] == "reconciliation_required"
    assert third["state"] == "provider_accepted"
    assert third["provider_reference"] == "jasmin-ref-unknown"
    assert third["submission_attempts"] == 1
    assert third["reconciliation_attempts"] == 3
    assert third["billing"]["state"] == "committed"
    assert transport.submit_calls == 1
    assert transport.readback_calls == 3

    usage = provider.usage(TENANT)
    assert usage["provider_submission_attempts"] == 1
    assert usage["provider_resubmissions"] == 0
    assert usage["reconciliation_readbacks"] == 3


def test_signed_dlr_replay_and_monotonic_state(tmp_path) -> None:
    transport = ScriptedJasminTransport(
        submissions=[SubmissionResult("accepted", "jasmin-ref-dlr", "accepted")]
    )
    provider = service(tmp_path, transport)
    submitted = provider.submit(command(key="sms-idempotency-dlr"))
    message_id = uuid.UUID(submitted["message_id"])

    delivered = DlrCallback(
        tenant_id=TENANT,
        message_id=message_id,
        provider_reference="jasmin-ref-dlr",
        provider_status="DELIVRD",
        occurred_at=datetime.now(UTC),
    )
    raw = canonical_json(delivered.model_dump(mode="json"))
    event_id = "jasmin-dlr-1"
    headers = callback_headers(CALLBACK_SECRET, event_id, raw)
    digest = verify_callback(
        secret=CALLBACK_SECRET,
        timestamp=headers["X-Telnexa-Timestamp"],
        event_id=event_id,
        raw_body=raw,
        signature=headers["X-Telnexa-Signature"],
    )

    first = provider.record_dlr(delivered, event_id=event_id, payload_digest=digest)
    replay = provider.record_dlr(delivered, event_id=event_id, payload_digest=digest)
    assert first == {
        "accepted": True,
        "duplicate": False,
        "status": "delivered",
        "ignored": False,
    }
    assert replay["duplicate"] is True

    changed = delivered.model_copy(update={"provider_status": "failed"})
    with pytest.raises(CallbackReplayConflict):
        provider.record_dlr(
            changed,
            event_id=event_id,
            payload_digest=sha256(canonical_json(changed.model_dump(mode="json"))),
        )

    regressive = delivered.model_copy(
        update={"provider_status": "failed", "occurred_at": datetime.now(UTC)}
    )
    regressive_result = provider.record_dlr(
        regressive,
        event_id="jasmin-dlr-2",
        payload_digest=sha256(canonical_json(regressive.model_dump(mode="json"))),
    )
    assert regressive_result["ignored"] is True
    assert regressive_result["status"] == "delivered"

    with provider.session_factory() as session:
        ignored = session.scalar(
            select(SmsProviderEvent).where(
                SmsProviderEvent.external_event_id == "jasmin-dlr-2"
            )
        )
        assert ignored is not None
        assert ignored.ignored_transition is True
        assert ignored.event_type == "sms.message.status.v1"


def test_inbound_stop_help_and_callback_outbox(tmp_path) -> None:
    provider = service(tmp_path, ScriptedJasminTransport())

    stop = MoCallback(
        tenant_id=TENANT,
        provider_message_id="mo-stop-1",
        sender="+18095550999",
        destination="+18095550000",
        content="STOP",
        occurred_at=datetime.now(UTC),
    )
    stop_raw = canonical_json(stop.model_dump(mode="json"))
    stop_result = provider.record_mo(
        stop,
        event_id="jasmin-mo-stop-1",
        payload_digest=sha256(stop_raw),
    )
    assert stop_result["action"] == "stop"
    assert provider.opt_outs(TENANT)[0]["phone"] == stop.sender

    help_request = stop.model_copy(
        update={
            "provider_message_id": "mo-help-1",
            "sender": "+18095550888",
            "content": "HELP",
        }
    )
    help_result = provider.record_mo(
        help_request,
        event_id="jasmin-mo-help-1",
        payload_digest=sha256(canonical_json(help_request.model_dump(mode="json"))),
    )
    assert help_result["action"] == "help"

    events = {item["event_type"] for item in provider.callbacks(TENANT)}
    assert "sms.recipient.opted-out.v1" in events
    assert "sms.help-requested.v1" in events

    with provider.session_factory() as session:
        assert session.query(SmsCallbackOutbox).count() == 2


def test_callback_authentication_and_middleware_signature_contract() -> None:
    body = json.dumps({"event": "delivered"}, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    headers = callback_headers(CALLBACK_SECRET, "event-1", body)
    assert verify_callback(
        secret=CALLBACK_SECRET,
        timestamp=timestamp,
        event_id="event-1",
        raw_body=body,
        signature=headers["X-Telnexa-Signature"],
    ) == sha256(body)

    with pytest.raises(CallbackAuthenticationError):
        verify_callback(
            secret=CALLBACK_SECRET,
            timestamp=timestamp,
            event_id="event-1",
            raw_body=body + b" ",
            signature=headers["X-Telnexa-Signature"],
        )

    payload = {"event_type": "sms.message.delivered.v1", "tenant_id": TENANT}
    raw, middleware_headers = middleware_callback(
        secret=MIDDLEWARE_TOKEN,
        event_id="event-2",
        payload=payload,
        timestamp=int(timestamp),
    )
    assert middleware_headers["X-Codestra-Event-Id"] == "event-2"
    assert middleware_headers["X-Codestra-Signature"].startswith("v1=")
    assert verify_callback(
        secret=MIDDLEWARE_TOKEN,
        timestamp=middleware_headers["X-Codestra-Timestamp"],
        event_id=middleware_headers["X-Codestra-Event-Id"],
        raw_body=raw,
        signature=middleware_headers["X-Codestra-Signature"],
    ) == sha256(raw)
