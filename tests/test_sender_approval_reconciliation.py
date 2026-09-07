"""Retain PR #11's acceptance test against current durable SMS source."""

import uuid
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from test_product_api import app, headers, seed
from test_product_api import clean as clean
from billing.models import Sender


def test_sender_registration_and_changed_payload_idempotency():
    db, tenant, account, key = seed()
    client = TestClient(app)
    request_headers = {
        **headers(tenant, key),
        "Idempotency-Key": "sender-approval-same-key",
        "X-Correlation-ID": str(uuid.uuid4()),
    }
    body = {
        "billing_account_id": account.id,
        "destination": "+491234567",
        "sender": "Telnexa",
        "content": "first",
    }
    denied = client.post("/api/v1/messages", headers=request_headers, json=body)
    assert denied.status_code >= 400
    assert denied.json()["detail"] == "sender_not_approved"
    db.add(Sender(tenant_id=tenant.id, sender="Telnexa", status="approved"))
    db.commit()
    first = client.post("/api/v1/messages", headers=request_headers, json=body)
    assert first.status_code == 202
    replay = client.post("/api/v1/messages", headers=request_headers, json=body)
    assert replay.status_code == 202
    assert replay.json() == first.json()
    changed = {**body, "content": "changed"}
    conflict = client.post("/api/v1/messages", headers=request_headers, json=changed)
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "idempotency_key_payload_mismatch"


def test_current_runtime_uses_canonical_disabled_delivery_controls():
    root = Path(__file__).resolve().parents[1]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text())
    environment = compose["services"]["billing-api"]["environment"]
    for flag in ("LIVE_SMS_DELIVERY", "LIVE_EMAIL_DELIVERY", "LIVE_PSTN_DIALING"):
        assert environment[flag] == "false"
    assert environment["TELNEXA_PRODUCTION_SMS_ENABLED"] == "false"
