"""Issue 29 no-effect API, callback, replay and interrupted-write regressions."""

import importlib
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from test_product_api import clean as clean
from test_product_api import app, headers, seed
from billing.db import SessionLocal
from billing.models import (
    Contact,
    InboundMessage,
    Message,
    Outbox,
    PhoneNumber,
    PricingPlan,
    Sender,
    SmsDispatchJob,
    SmsProviderEventInbox,
    Tenant,
    Wallet,
    Webhook,
    WebhookDelivery,
)
from billing.provider_events import process_event, signature
from billing.sms_integration import SmsAcceptanceReceipt, SmsInboundBinding
from billing.webhooks import encrypt_secret

api = importlib.import_module("billing.app")


@pytest.fixture
def context():
    db, tenant, account, key = seed()
    db.add(Sender(tenant_id=tenant.id, sender="Telnexa", status="approved"))
    db.commit()
    body = {
        "billing_account_id": account.id,
        "destination": "+491234567",
        "sender": "Telnexa",
        "content": "synthetic contract message",
        "client_reference": "synthetic-command-29",
    }
    request_headers = {
        **headers(tenant, key),
        "Idempotency-Key": "issue29-stable-command",
        "X-Correlation-ID": str(uuid.uuid4()),
    }
    yield db, tenant, account, key, body, request_headers
    db.close()


def test_acceptance_bytes_survive_delivery_transition_and_readback_is_current(context):
    db, tenant, account, key, body, h = context
    client = TestClient(app)
    accepted = client.post("/api/v1/messages", headers=h, json=body)
    assert accepted.status_code == 202
    message_id = accepted.json()["message_id"]
    db.expire_all()
    message = db.get(Message, message_id)
    message.status = "delivered"
    message.provider_message_id = "synthetic-provider-id"
    db.commit()
    replay = client.post("/api/v1/messages", headers=h, json=body)
    assert replay.status_code == 202 and replay.content == accepted.content
    current = client.get("/api/v1/messages/by-idempotency", headers=h)
    assert current.status_code == 200
    data = current.json()
    assert data["status"] == "delivered" and data["message_id"] == message_id
    assert data["idempotency_key"] == h["Idempotency-Key"]
    assert data["client_reference"] == "synthetic-command-29"
    assert data["request_hash"] == message.request_hash
    assert data["acceptance_snapshot_available"] is True
    assert not {"content", "destination", "sender", "api_key"}.intersection(data)
    assert db.query(Message).count() == db.query(SmsDispatchJob).count() == 1
    assert db.query(SmsAcceptanceReceipt).count() == 1


def test_missing_readback_does_not_create_a_submission(context):
    db, tenant, account, key, body, h = context
    assert TestClient(app).get("/api/v1/messages/by-idempotency", headers=h).status_code == 404
    assert db.query(Message).count() == db.query(SmsDispatchJob).count() == 0
    assert db.query(Wallet).one().reserved == 0


def test_changed_command_fingerprint_is_a_conflict(context):
    *_, body, h = context
    client = TestClient(app)
    assert client.post("/api/v1/messages", headers=h, json=body).status_code == 202
    for field, value in (
        ("content", "different"),
        ("client_reference", "different"),
        ("category", "service"),
    ):
        response = client.post("/api/v1/messages", headers=h, json={**body, field: value})
        assert response.status_code == 409
        assert response.json()["detail"] == "idempotency_key_payload_mismatch"


def test_unknown_key_and_cross_tenant_readback_are_denied(context):
    db, tenant, account, key, body, h = context
    client = TestClient(app)
    assert client.post("/api/v1/messages", headers=h, json=body).status_code == 202
    assert (
        client.get(
            "/api/v1/messages/by-idempotency", headers={**h, "X-API-Key": "wrong"}
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/messages/by-idempotency", headers={**h, "X-Tenant-ID": str(uuid.uuid4())}
        ).status_code
        == 401
    )


def test_status_scope_does_not_read_billing_or_send(context):
    db, tenant, account, key, body, h = context
    from billing.models import ApiKey

    client = TestClient(app)
    assert client.post("/api/v1/messages", headers=h, json=body).status_code == 202
    db.query(ApiKey).one().scopes = "sms.status.read"
    db.commit()
    assert client.get("/api/v1/messages/by-idempotency", headers=h).status_code == 200
    assert client.get("/api/v1/billing/wallet", headers=h).status_code == 403
    assert client.get("/api/v1/contacts", headers=h).status_code == 403
    assert client.get("/api/v1/integration/health", headers=h).status_code == 403
    assert client.post("/api/v1/messages", headers=h, json=body).status_code == 403


def test_receipt_and_submission_are_one_transaction(context, monkeypatch):
    db, tenant, account, key, body, h = context

    def fail_receipt(*args, **kwargs):
        raise ValueError("synthetic_receipt_write_failure")

    monkeypatch.setattr(api, "store_receipt", fail_receipt)
    assert TestClient(app).post("/api/v1/messages", headers=h, json=body).status_code == 402
    assert db.query(Message).count() == db.query(SmsDispatchJob).count() == 0
    assert db.query(SmsAcceptanceReceipt).count() == 0
    db.expire_all()
    assert db.query(Wallet).one().reserved == 0


def test_legacy_receipt_is_not_fabricated(context):
    db, tenant, account, key, body, h = context
    client = TestClient(app)
    response = client.post("/api/v1/messages", headers=h, json=body)
    # SQLite unit fixture deliberately models a message created before migration.
    db.delete(db.get(SmsAcceptanceReceipt, response.json()["message_id"]))
    db.commit()
    replay = client.post("/api/v1/messages", headers=h, json=body)
    assert replay.status_code == 409
    assert replay.json()["detail"] == "legacy_acceptance_requires_readback"
    assert (
        client.get("/api/v1/messages/by-idempotency", headers=h).json()[
            "acceptance_snapshot_available"
        ]
        is False
    )
    assert db.query(SmsDispatchJob).count() == 1


@pytest.mark.parametrize("category", ["transactional", "service", "marketing"])
def test_suppression_prevents_all_nonexempt_categories(context, category):
    db, tenant, account, key, body, h = context
    db.add(Contact(tenant_id=tenant.id, phone=body["destination"], consent_status="opted_out"))
    db.commit()
    response = TestClient(app).post(
        "/api/v1/messages", headers=h, json={**body, "category": category}
    )
    assert response.status_code == 409
    assert db.query(Message).count() == db.query(SmsDispatchJob).count() == 0


def test_campaign_reference_cannot_cross_tenant_or_bypass_approval(context):
    db, tenant, account, key, body, h = context
    from billing.models import Campaign

    other = Tenant(name="Other synthetic tenant", status="active")
    db.add(other)
    db.flush()
    campaign = Campaign(
        tenant_id=other.id, name="Other campaign", status="approved", category="transactional"
    )
    db.add(campaign)
    db.commit()
    client = TestClient(app)
    assert (
        client.post(
            "/api/v1/messages", headers=h, json={**body, "campaign_id": campaign.id}
        ).status_code
        == 403
    )
    campaign.tenant_id, campaign.status = tenant.id, "draft"
    db.commit()
    assert (
        client.post(
            "/api/v1/messages", headers=h, json={**body, "campaign_id": campaign.id}
        ).status_code
        == 403
    )
    assert db.query(Message).count() == 0


def test_rate_budget_does_not_charge_replay(context):
    db, tenant, account, key, body, h = context
    plan = db.query(PricingPlan).one()
    tenant.plan_id = plan.id
    plan.http_tps = 1
    db.commit()
    client = TestClient(app)
    first = client.post("/api/v1/messages", headers=h, json=body)
    assert first.status_code == 202
    assert client.post("/api/v1/messages", headers=h, json=body).content == first.content
    limited = client.post(
        "/api/v1/messages", headers={**h, "Idempotency-Key": "new-command"}, json=body
    )
    assert limited.status_code == 429 and limited.headers["retry-after"] == "1"
    assert db.query(Message).count() == 1


def test_monthly_quota_applies_to_distinct_commands(context):
    db, tenant, account, key, body, h = context
    plan = db.query(PricingPlan).one()
    tenant.plan_id = plan.id
    plan.http_tps, plan.monthly_quota = 100, 1
    db.commit()
    client = TestClient(app)
    assert client.post("/api/v1/messages", headers=h, json=body).status_code == 202
    assert (
        client.post(
            "/api/v1/messages", headers={**h, "Idempotency-Key": "quota-second"}, json=body
        ).json()["detail"]
        == "sms_monthly_quota_exceeded"
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("Idempotency-Key", "x" * 181),
        ("Idempotency-Key", ""),
        ("X-Correlation-ID", "x" * 37),
    ],
)
def test_reference_bounds(context, field, value):
    *_, body, h = context
    assert (
        TestClient(app).post("/api/v1/messages", headers={**h, field: value}, json=body).status_code
        == 422
    )


def test_maximum_idempotency_header_fits_derived_billing_key(context):
    db, tenant, account, key, body, h = context
    assert (
        TestClient(app)
        .post("/api/v1/messages", headers={**h, "Idempotency-Key": "x" * 180}, json=body)
        .status_code
        == 202
    )


def test_oversized_send_body_is_rejected_before_model_parsing(context):
    *_, body, h = context
    response = TestClient(app).post(
        "/api/v1/messages", headers={**h, "Content-Type": "application/json"}, content=b"x" * 65537
    )
    assert response.status_code == 413


def test_health_does_not_claim_live_provider_or_staging_certification(context, monkeypatch):
    *_, h = context
    monkeypatch.setenv("SOURCE_SHA", "a" * 40)
    result = TestClient(app).get("/api/v1/integration/health", headers=h)
    assert result.status_code == 200
    assert result.json()["provider_connectivity"] == "not_probed"
    assert result.json()["runtime_certified"] is False
    assert result.json()["production_sms"] is False


def callback_client(monkeypatch, tmp_path):
    secret = b"synthetic-provider-callback-key-29-only"
    path = tmp_path / "callback-key"
    path.write_bytes(secret)
    monkeypatch.setenv("TELNEXA_PROVIDER_EVENT_HMAC_SECRET_FILE", str(path))
    client = TestClient(app)

    def post(
        payload, source="jasmin-primary", event_id="synthetic-dlr-29", timestamp=None, mutate=None
    ):
        raw = (
            payload
            if isinstance(payload, bytes)
            else json.dumps(payload, separators=(",", ":")).encode()
        )
        timestamp = timestamp or str(int(time.time()))
        h = {
            "Content-Type": "application/json",
            "X-Key-ID": source,
            "X-Signature-Version": "v2",
            "X-Telnexa-Timestamp": timestamp,
            "X-Telnexa-Event-Id": event_id,
            "X-Telnexa-Signature": "sha256="
            + signature(
                secret,
                "POST",
                "/internal/v1/provider-events/jasmin",
                timestamp,
                event_id,
                raw,
                source,
            ),
        }
        h.update(mutate or {})
        return client.post("/internal/v1/provider-events/jasmin", headers=h, content=raw)

    return post


def test_v2_callback_authentication_and_replay(context, monkeypatch, tmp_path):
    db, *_ = context
    post = callback_client(monkeypatch, tmp_path)
    payload = {"event": "dlr", "data": {"id": "provider-29", "status": "DELIVRD"}}
    assert post(payload, mutate={"X-Key-ID": "different-source"}).status_code == 401
    assert post(payload, mutate={"X-Signature-Version": "v1"}).status_code == 401
    assert post(payload, timestamp=str(int(time.time()) - 301)).status_code == 401
    assert post(payload).status_code == 202
    assert post(payload).json()["duplicate"] is True
    changed = {"event": "dlr", "data": {"id": "provider-29", "status": "FAILED"}}
    assert post(changed).status_code == 409
    assert db.query(SmsProviderEventInbox).count() == 1


@pytest.mark.parametrize(
    "payload",
    [
        b"[]",
        b'{"event":"dlr","data":[]}',
        b'{"event":"dlr","event":"inbound","data":{"id":"p"}}',
        b'{"event":"dlr","data":{"id":"p","value":NaN}}',
        b'{"event":"inbound","data":{"id":"p","content":123}}',
        b"[" * 2000 + b"]" * 2000,
    ],
)
def test_signed_malformed_callbacks_fail_without_server_error(
    context, monkeypatch, tmp_path, payload
):
    assert callback_client(monkeypatch, tmp_path)(payload).status_code == 422


def test_dlr_reaches_local_state_and_deliverable_outbox_once(context, monkeypatch, tmp_path):
    db, tenant, account, key, body, h = context
    accepted = TestClient(app).post("/api/v1/messages", headers=h, json=body)
    message = db.get(Message, accepted.json()["message_id"])
    message.status, message.provider_message_id = "submitted", "provider-29"
    hook = Webhook(
        tenant_id=tenant.id,
        url="https://example.invalid/callback",
        events=["sms.delivered"],
        secret_hash="a" * 64,
        secret_ciphertext=encrypt_secret("synthetic-webhook-secret-29"),
        enabled=True,
    )
    db.add(hook)
    db.commit()
    post = callback_client(monkeypatch, tmp_path)
    payload = {"event": "dlr", "data": {"id": "provider-29", "status": "DELIVRD"}}
    assert post(payload).status_code == 202
    row = db.query(SmsProviderEventInbox).one()
    process_event(db, row)
    db.commit()
    process_event(db, row)
    db.commit()
    assert post(payload).json()["duplicate"] is True
    db.expire_all()
    assert db.get(Message, message.id).status == "delivered"
    assert db.query(Outbox).filter_by(event_type="sms.delivered").count() == 1
    delivery = db.query(WebhookDelivery).one()
    assert db.get(Outbox, delivery.event_id) is not None
    from billing import worker

    sent = []
    monkeypatch.setattr(worker, "validate_webhook_url", lambda url: url)
    worker.deliver_webhooks(
        db, sender=lambda request, **kwargs: sent.append(json.loads(request.data))
    )
    db.commit()
    worker.deliver_webhooks(
        db, sender=lambda request, **kwargs: sent.append(json.loads(request.data))
    )
    assert len(sent) == 1 and sent[0]["type"] == "sms.delivered"
    assert sent[0]["data"]["message_id"] == message.id


def test_mo_binding_is_explicit_and_stop_is_durable_during_middleware_outage(
    context, monkeypatch, tmp_path
):
    db, tenant, account, key, body, h = context
    number = PhoneNumber(
        tenant_id=tenant.id, account_id=account.id, number="+498765432", status="active"
    )
    db.add(number)
    db.commit()
    post = callback_client(monkeypatch, tmp_path)
    payload = {
        "event": "inbound",
        "data": {
            "id": "inbound-29",
            "from": body["destination"],
            "to": number.number,
            "content": "STOP",
            "tenant_id": "spoofed",
        },
    }
    assert post(payload, event_id="unbound-29").status_code == 202
    row = db.query(SmsProviderEventInbox).one()
    process_event(db, row)
    db.commit()
    assert row.state == "quarantined" and db.query(InboundMessage).count() == 0
    db.add(
        SmsInboundBinding(
            source_key_id="jasmin-primary",
            destination=number.number,
            number_id=number.id,
            enabled=True,
        )
    )
    db.commit()
    assert post(payload, event_id="bound-29").status_code == 202
    row = db.query(SmsProviderEventInbox).filter_by(event_id="bound-29").one()
    process_event(db, row)
    db.commit()
    contact = db.query(Contact).one()
    assert contact.tenant_id == tenant.id and contact.consent_status == "opted_out"
    assert db.query(InboundMessage).one().tenant_id == tenant.id
    event = db.query(Outbox).filter_by(event_type="sms.opted_out").one()
    assert event.envelope["payload"]["inbound_message_id"]
    assert event.state == "pending"  # No Middleware call was needed to suppress.
    assert TestClient(app).post("/api/v1/messages", headers=h, json=body).status_code == 409


def test_dispatch_claim_commits_before_interrupted_provider_processing(context, monkeypatch):
    db, tenant, account, key, body, h = context
    accepted = TestClient(app).post("/api/v1/messages", headers=h, json=body)
    from billing import dispatch_worker

    calls = []
    monkeypatch.setattr(dispatch_worker, "production_enabled", lambda: True)

    def interrupted(session, job, adapter):
        with SessionLocal() as observer:
            assert observer.get(SmsDispatchJob, job.id).state == "dispatching"
        calls.append(job.id)
        raise RuntimeError("synthetic interruption after durable claim")

    monkeypatch.setattr(dispatch_worker, "process_job", interrupted)
    with pytest.raises(RuntimeError):
        dispatch_worker.run_once("synthetic-worker")
    assert dispatch_worker.run_once("restarted-worker") is False
    db.expire_all()
    assert len(calls) == 1
    assert db.query(SmsDispatchJob).one().state == "reconciliation"
    assert db.query(Message).one().status == "submission_unknown"
    assert (
        TestClient(app).post("/api/v1/messages", headers=h, json=body).content == accepted.content
    )


def test_expired_dispatch_claim_is_quarantined_not_requeued(context):
    db, tenant, account, key, body, h = context
    TestClient(app).post("/api/v1/messages", headers=h, json=body)
    from billing.dispatch import claim_job
    from billing.reconciliation import scan

    job = claim_job(db, "interrupted-worker")
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    scan(db)
    db.commit()
    assert job.state == "reconciliation"
    assert claim_job(db, "replacement-worker") is None
    assert db.query(Message).one().status == "submission_unknown"


def test_chunked_payload_is_bounded_before_application():
    import asyncio
    from billing.bounded_body import BoundedBodyMiddleware

    async def exercise():
        called, sent = [], []
        chunks = iter(
            [
                {"type": "http.request", "body": b"a" * 65530, "more_body": True},
                {"type": "http.request", "body": b"b" * 10, "more_body": False},
            ]
        )

        async def receive():
            return next(chunks)

        async def send(event):
            sent.append(event)

        async def application(scope, receive, send):
            called.append(True)

        scope = {"type": "http", "method": "POST", "path": "/api/v1/messages", "headers": []}
        await BoundedBodyMiddleware(application)(scope, receive, send)
        assert not called
        assert sent[0]["status"] == 413

    asyncio.run(exercise())
