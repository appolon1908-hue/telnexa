import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

os.environ["BILLING_DATABASE_URL"] = "sqlite:////tmp/telnexa-product-tests.db"
os.environ["BILLING_ADMIN_TOKEN"] = "test-admin-token"
os.environ["BILLING_JWT_SECRET"] = "test-only-webhook-encryption-key-32-bytes-minimum"
from fastapi.testclient import TestClient
from sqlalchemy.orm import close_all_sessions
from billing.app import app, ph
from billing.db import Base, engine, SessionLocal
from billing.models import *
import pytest


@pytest.fixture(autouse=True)
def clean():
    close_all_sessions()
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    close_all_sessions()


def seed(scopes="admin"):
    db = SessionLocal()
    p = PricingPlan(name="Test")
    t = Tenant(name="Tenant", status="active", plan_id=p.id)
    db.add_all([p, t])
    db.flush()
    a = BillingAccount(tenant_id=t.id, currency="EUR")
    db.add(a)
    db.flush()
    db.add(
        Wallet(
            tenant_id=t.id,
            billing_account_id=a.id,
            currency="EUR",
            available=Decimal("100"),
        )
    )
    raw = "tnx_" + "a" * 32
    db.add(
        ApiKey(tenant_id=t.id, prefix=raw[:12], secret_hash=ph.hash(raw), scopes=scopes)
    )
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            Rate(
                kind="provider",
                country="ZZ",
                prefix="+",
                currency="EUR",
                amount=Decimal(".02"),
                provider="simulator",
                effective_from=now - timedelta(days=1),
            ),
            Rate(
                kind="sell",
                country="ZZ",
                prefix="+",
                currency="EUR",
                amount=Decimal(".04"),
                effective_from=now - timedelta(days=1),
            ),
        ]
    )
    db.commit()
    return db, t, a, raw


def headers(t, key):
    return {"X-Tenant-ID": t.id, "X-API-Key": key}


def test_contact_consent_sender_and_campaign_guardrails():
    db, t, a, key = seed()
    c = TestClient(app)
    h = headers(t, key)
    assert (
        c.post(
            "/api/v1/contacts",
            headers=h,
            json={"phone": "+491234567", "consent_status": "opted_in"},
        ).status_code
        == 422
    )
    assert (
        c.post(
            "/api/v1/contacts",
            headers=h,
            json={
                "phone": "+491234567",
                "consent_status": "opted_in",
                "consent_source": "contract",
            },
        ).status_code
        == 201
    )
    sender = c.post(
        "/api/v1/senders", headers=h, json={"sender": "Telnexa", "countries": ["DE"]}
    )
    assert sender.json()["carrier_approved"] is False
    campaign = c.post(
        "/api/v1/campaigns",
        headers=h,
        json={"name": "Opt-in update", "category": "marketing", "recipient_count": 5},
    )
    assert (
        campaign.json()["status"] == "pending_approval"
        and campaign.json()["dispatch_enabled"] is False
    )


def test_marketing_suppression_and_sender_approval():
    db, t, a, key = seed()
    c = TestClient(app)
    h = {**headers(t, key), "Idempotency-Key": "m1"}
    db.add(
        Contact(
            tenant_id=t.id,
            phone="+491234567",
            consent_status="opted_out",
            opted_out_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    body = {
        "billing_account_id": a.id,
        "destination": "+491234567",
        "sender": "Telnexa",
        "content": "offer",
        "category": "marketing",
    }
    assert c.post("/api/v1/messages", headers=h, json=body).status_code == 409
    db.query(Contact).delete()
    db.add(
        Contact(
            tenant_id=t.id,
            phone="+491234567",
            consent_status="opted_in",
            consent_source="contract",
        )
    )
    db.add(Sender(tenant_id=t.id, sender="Telnexa", status="requested"))
    db.commit()
    assert (
        c.post("/api/v1/messages", headers=h, json=body).json()["detail"]
        == "sender_not_approved"
    )


def test_missing_sender_and_changed_idempotency_payload_are_denied():
    db, t, a, key = seed()
    c = TestClient(app)
    h = {**headers(t, key), "Idempotency-Key": "stable-key"}
    body = {
        "billing_account_id": a.id,
        "destination": "+491234567",
        "sender": "Telnexa",
        "content": "first",
        "category": "transactional",
    }
    assert (
        c.post("/api/v1/messages", headers=h, json=body).json()["detail"]
        == "sender_not_approved"
    )
    db.add(Sender(tenant_id=t.id, sender="Telnexa", status="approved"))
    db.commit()
    assert c.post("/api/v1/messages", headers=h, json=body).status_code == 202
    assert (
        c.post(
            "/api/v1/messages", headers=h, json={**body, "content": "changed"}
        ).status_code
        == 409
    )


def test_simulator_mo_stop_help_and_deduplication():
    db, t, a, key = seed()
    c = TestClient(app)
    h = {"X-Simulator-Token": "test-admin-token"}
    body = {
        "tenant_id": t.id,
        "provider_message_id": "mo-1",
        "sender": "+491234567",
        "destination": "+498765432",
        "content": "STOPALL",
    }
    r = c.post("/api/v1/simulator/mo", headers=h, json=body)
    assert r.status_code == 202 and r.json()["event"] == "sms.opted_out"
    assert (
        c.post("/api/v1/simulator/mo", headers=h, json=body).json()["duplicate"] is True
    )
    body.update(provider_message_id="mo-2", content="HELP")
    assert (
        c.post("/api/v1/simulator/mo", headers=h, json=body).json()["event"]
        == "sms.help_requested"
    )


def test_dlr_timeline_is_tenant_scoped_and_idempotent():
    db, t, a, key = seed()
    m = Message(
        tenant_id=t.id,
        idempotency_key="x",
        request_hash="x",
        correlation_id="c",
        destination="+491234567",
        sender="T",
        content_hash="x",
        encoding="GSM-7",
        character_count=1,
        segments=1,
        provider="simulator",
        status="sent",
        provider_rate_snapshot={},
        sell_rate_snapshot={},
        estimated_provider_cost=Decimal(".02"),
        estimated_sell_amount=Decimal(".04"),
    )
    db.add(m)
    db.commit()
    c = TestClient(app)
    h = {"X-Simulator-Token": "test-admin-token"}
    body = {
        "tenant_id": t.id,
        "message_id": m.id,
        "external_event_id": "dlr-1",
        "status": "delivered",
    }
    assert (
        c.post("/api/v1/simulator/dlr", headers=h, json=body).json()["duplicate"]
        is False
    )
    assert (
        c.post("/api/v1/simulator/dlr", headers=h, json=body).json()["duplicate"]
        is True
    )
    assert (
        len(
            c.get(f"/api/v1/messages/{m.id}/events", headers=headers(t, key)).json()[
                "items"
            ]
        )
        == 1
    )


def test_failover_preview_ignores_open_circuit():
    db, t, a, key = seed()
    p1 = Provider(
        name="Primary",
        connector="sim-primary",
        state="enabled",
        health_score=10,
        circuit_state="open",
    )
    p2 = Provider(
        name="Simulator backup",
        connector="simulator",
        state="enabled",
        health_score=95,
        circuit_state="closed",
    )
    db.add_all([p1, p2])
    db.flush()
    db.add_all(
        [
            Route(
                country="DE", prefix="+49", provider_id=p1.id, priority=10, enabled=True
            ),
            Route(
                country="DE", prefix="+49", provider_id=p2.id, priority=5, enabled=True
            ),
        ]
    )
    db.commit()
    c = TestClient(app)
    r = c.get(
        "/api/v1/admin/routes/preview?destination=%2B49123",
        headers={"X-Admin-Token": "test-admin-token"},
    )
    assert (
        r.json()["selected"]["provider"] == "Simulator backup"
        and r.json()["dry_run"] is True
    )


def test_webhook_secret_is_displayed_once():
    db, t, a, key = seed()
    c = TestClient(app)
    r = c.post(
        "/api/v1/webhooks",
        headers=headers(t, key),
        json={"url": "https://example.com/hook", "events": ["sms.delivered"]},
    )
    assert r.status_code == 201 and r.json()["secret"].startswith("whsec_")
    assert db.query(Webhook).one().secret_ciphertext.startswith("gAAAA")


def test_login_secure_session_logout_and_rate_limit():
    db, t, a, key = seed()
    db.add(
        TeamMember(
            tenant_id=t.id,
            email="user@example.com",
            display_name="User",
            role="tenant_admin",
            password_hash=ph.hash("a-strong-password"),
            status="active",
            email_verified=True,
        )
    )
    db.commit()
    c = TestClient(app, base_url="https://testserver")
    r = c.post(
        "/api/v1/auth/login",
        json={
            "tenant_id": t.id,
            "email": "user@example.com",
            "password": "a-strong-password",
        },
    )
    assert r.status_code == 200 and r.json()["csrf_token"]
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "Secure" in cookie and "SameSite=strict" in cookie
    assert c.post("/api/v1/auth/logout").status_code == 204
    assert db.query(AuthToken).one().revoked_at is not None
    for _ in range(5):
        assert (
            c.post(
                "/api/v1/auth/login",
                json={
                    "tenant_id": t.id,
                    "email": "user@example.com",
                    "password": "wrong-password",
                },
            ).status_code
            == 401
        )
    assert (
        c.post(
            "/api/v1/auth/login",
            json={
                "tenant_id": t.id,
                "email": "user@example.com",
                "password": "wrong-password",
            },
        ).status_code
        == 429
    )
