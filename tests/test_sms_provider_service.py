from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from billing.sms_provider_runtime import (
    ReadbackResult,
    ScriptedJasminTransport,
    SmsProviderService,
    SubmissionResult,
    canonical_json,
    create_session_factory,
    segment_info,
)
from billing.sms_provider_service import create_app

TOKEN = "middleware-stage4-token-000000000000000000000"
CALLBACK_SECRET = "provider-callback-stage4-000000000000000000"
TENANT = "tenant-stage4"


def build_client(tmp_path) -> tuple[TestClient, ScriptedJasminTransport]:
    os.environ["TELNEXA_MIDDLEWARE_BEARER_TOKEN"] = TOKEN
    os.environ["TELNEXA_PROVIDER_CALLBACK_SECRET"] = CALLBACK_SECRET
    _, factory = create_session_factory(f"sqlite:///{tmp_path / 'service.db'}")
    transport = ScriptedJasminTransport(
        submissions=[SubmissionResult("unknown", error="timeout_after_acceptance")],
        readbacks=[
            ReadbackResult("unknown", error="readback_unavailable"),
            ReadbackResult("accepted", "jasmin-service-ref", "accepted"),
        ],
    )
    app = create_app(SmsProviderService(factory, transport))
    return TestClient(app), transport


def command() -> dict:
    content = "Service test"
    info = segment_info(content)
    message_id = uuid.uuid4()
    return {
        "command_id": str(uuid.uuid4()),
        "command_type": "sms.message.submit.v1",
        "command_version": "1.0",
        "target": "telnexa-sms",
        "tenant_id": TENANT,
        "requested_by": "middleware-stage4",
        "correlation_id": "corr-service-1",
        "idempotency_key": "service-idempotency-0001",
        "capability": "SMS_DELIVERY",
        "payload": {
            "message_id": str(message_id),
            "channel": "sms",
            "destination": "+18095550123",
            "sender": "CODESTRA",
            "content": content,
            "encoding": info.encoding,
            "characters": info.characters,
            "segments": info.segments,
            "category": "transactional",
            "client_reference": str(message_id),
            "scheduled_at": None,
            "billing_account_id": "billing-stage4",
            "campaign_id": None,
        },
    }


def auth() -> dict[str, str]:
    return {"Authorization": "Bearer " + TOKEN, "X-Tenant-ID": TENANT}


def callback_headers(event_id: str, body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        CALLBACK_SECRET.encode(),
        timestamp.encode() + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Telnexa-Timestamp": timestamp,
        "X-Telnexa-Event-Id": event_id,
        "X-Telnexa-Signature": "sha256=" + signature,
    }


def test_private_api_auth_idempotency_reconciliation_and_callbacks(tmp_path) -> None:
    client, transport = build_client(tmp_path)
    payload = command()

    assert client.post("/api/v1/provider/operations", json=payload).status_code == 401
    submitted = client.post("/api/v1/provider/operations", json=payload, headers=auth())
    assert submitted.status_code == 202
    operation = submitted.json()
    assert operation["state"] == "reconciliation_required"
    assert operation["submission_attempts"] == 1

    replay = client.post("/api/v1/commands/sms.message.submit.v1", json=payload, headers=auth())
    assert replay.status_code == 202
    assert replay.json()["replay"] is True
    assert transport.submit_calls == 1

    first = client.post(
        f"/api/v1/provider/operations/{operation['operation_id']}/reconcile",
        headers=auth(),
    )
    second = client.post(
        f"/api/v1/provider/operations/{operation['operation_id']}/reconcile",
        headers=auth(),
    )
    assert first.json()["state"] == "reconciliation_required"
    assert second.json()["state"] == "provider_accepted"
    assert transport.submit_calls == 1
    assert transport.readback_calls == 2

    dlr = {
        "tenant_id": TENANT,
        "message_id": operation["message_id"],
        "provider_reference": "jasmin-service-ref",
        "provider_status": "DELIVRD",
        "occurred_at": datetime.now(UTC).isoformat(),
        "failure_code": None,
        "failure_message": None,
    }
    raw = canonical_json(dlr)
    dlr_response = client.post(
        "/api/v1/provider/callbacks/dlr",
        content=raw,
        headers=callback_headers("service-dlr-1", raw),
    )
    assert dlr_response.status_code == 202
    assert dlr_response.json()["status"] == "delivered"

    stop = {
        "tenant_id": TENANT,
        "provider_message_id": "service-mo-1",
        "sender": "+18095550999",
        "destination": "+18095550000",
        "content": "STOP",
        "occurred_at": datetime.now(UTC).isoformat(),
    }
    stop_raw = canonical_json(stop)
    mo_response = client.post(
        "/api/v1/provider/callbacks/mo",
        content=stop_raw,
        headers=callback_headers("service-mo-1", stop_raw),
    )
    assert mo_response.status_code == 202
    assert mo_response.json()["action"] == "stop"

    usage = client.get("/api/v1/provider/usage", headers=auth()).json()
    assert usage["provider_submission_attempts"] == 1
    assert usage["provider_resubmissions"] == 0
    assert client.get("/api/v1/provider/opt-outs", headers=auth()).json()["items"]
    assert client.get("/health").json()["jasmin_live_submission"] is False
