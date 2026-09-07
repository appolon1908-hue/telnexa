"""Disposable PostgreSQL certification; no production host or provider transport.

Run as `python -m scripts.certify_private_sms_contract` after billing.migrate.
The guard intentionally permits only the dedicated localhost CI database.
"""

import json
import os
import socket
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit


def main():
    url = urlsplit(os.environ.get("BILLING_DATABASE_URL", ""))
    if (
        os.environ.get("TELNEXA_DISPOSABLE_CERTIFICATION") != "true"
        or url.hostname not in {"127.0.0.1", "localhost"}
        or url.path != "/telnexa_issue29_ci"
        or url.username != "telnexa_ci"
        or url.scheme != "postgresql+psycopg"
    ):
        raise SystemExit("dedicated_loopback_disposable_database_required")
    for name in (
        "LIVE_SMS_DELIVERY",
        "LIVE_EMAIL_DELIVERY",
        "LIVE_PSTN_DIALING",
        "TELNEXA_PRODUCTION_SMS_ENABLED",
        "SMS_DELIVERY",
    ):
        if os.environ.get(name) != "false":
            raise SystemExit("all_effect_flags_must_be_explicitly_false")
    sha = os.environ.get("SOURCE_SHA", "")
    if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
        raise SystemExit("exact_source_sha_required")

    # Drivers may use libpq sockets for the permitted disposable DB; Python
    # HTTP/provider transports cannot connect to any external address.
    original_connect = socket.socket.connect

    def loopback_only(sock, address):
        if isinstance(address, tuple) and address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("external_connection_forbidden")
        return original_connect(sock, address)

    socket.socket.connect = loopback_only
    from fastapi.testclient import TestClient
    from sqlalchemy import select, text
    from sqlalchemy.exc import DBAPIError
    from billing.app import app, ph
    from billing.db import SessionLocal, engine
    from billing.models import (
        ApiKey,
        BillingAccount,
        CountryPolicy,
        Message,
        PricingPlan,
        Provider,
        Rate,
        Route,
        Sender,
        SmsDispatchJob,
        Tenant,
        Wallet,
    )
    from billing.sms_integration import SmsAcceptanceReceipt

    with SessionLocal.begin() as db:
        plan = PricingPlan(name="issue29-disposable", http_tps=100, monthly_quota=100)
        db.add(plan)
        db.flush()
        tenant = Tenant(name="Synthetic issue29", status="active", plan_id=plan.id)
        db.add(tenant)
        db.flush()
        account = BillingAccount(tenant_id=tenant.id, currency="EUR")
        db.add(account)
        db.flush()
        db.add(
            Wallet(
                tenant_id=tenant.id,
                billing_account_id=account.id,
                currency="EUR",
                available=Decimal("100"),
            )
        )
        raw_key = "tnx_" + uuid.uuid4().hex
        db.add(
            ApiKey(
                tenant_id=tenant.id,
                account_id=account.id,
                prefix=raw_key[:12],
                secret_hash=ph.hash(raw_key),
                scopes="sms.send sms.status.read",
            )
        )
        provider = Provider(
            name="issue29-synthetic-provider",
            connector="issue29-synthetic",
            state="enabled",
            routing_enabled=True,
            adapter_type="jasmin_http",
            credential_reference="/run/secrets/NOT_INSTALLED",
            dlr_source_key_id="issue29-synthetic",
            health_score=1,
        )
        db.add(provider)
        db.flush()
        db.add(Route(country="DE", prefix="+49", provider_id=provider.id, priority=1, enabled=True))
        for kind in ("provider", "sell"):
            db.add(
                Rate(
                    kind=kind,
                    country="DE",
                    prefix="+49",
                    currency="EUR",
                    amount=Decimal("0.04"),
                    provider=provider.name if kind == "provider" else None,
                    effective_from=datetime.now(timezone.utc) - timedelta(days=1),
                )
            )
        db.add(CountryPolicy(country="DE", category="transactional", enabled=True, config={}))
        db.add(Sender(tenant_id=tenant.id, sender="Telnexa", status="approved"))
        tenant_id, account_id, plan_id = tenant.id, account.id, plan.id

    headers = {
        "X-API-Key": raw_key,
        "X-Tenant-ID": tenant_id,
        "X-Correlation-ID": str(uuid.uuid4()),
    }
    body = {
        "billing_account_id": account_id,
        "destination": "+491234567",
        "sender": "Telnexa",
        "content": "synthetic no-effect fixture",
    }

    def submit(pair):
        key, content = pair
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/messages",
                headers={**headers, "Idempotency-Key": key},
                json={**body, "content": content},
            )
        return response.status_code, response.content

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(submit, [("same-command", "same payload")] * 4))
    assert {status for status, _ in results} == {202}
    assert len({content for _, content in results}) == 1
    with SessionLocal() as db:
        assert (
            db.query(Message).count()
            == db.query(SmsDispatchJob).count()
            == db.query(SmsAcceptanceReceipt).count()
            == 1
        )
    with ThreadPoolExecutor(max_workers=2) as pool:
        changed = list(pool.map(submit, [("changed-command", "one"), ("changed-command", "two")]))
    assert sorted(status for status, _ in changed) == [202, 409]
    with SessionLocal() as db:
        assert (
            db.query(Message).count()
            == db.query(SmsDispatchJob).count()
            == db.query(SmsAcceptanceReceipt).count()
            == 2
        )
        row = db.scalar(
            select(SmsAcceptanceReceipt).where(
                SmsAcceptanceReceipt.idempotency_key == "same-command"
            )
        )
        saved_id = row.message_id
    for statement in (
        "UPDATE sms_acceptance_receipts SET response_text = '{}' WHERE message_id = :id",
        "DELETE FROM sms_acceptance_receipts WHERE message_id = :id",
    ):
        try:
            with engine.begin() as conn:
                conn.execute(text(statement), {"id": saved_id})
        except DBAPIError:
            pass
        else:
            raise AssertionError("immutable_receipt_guard_missing")
    with TestClient(app) as client:
        before = client.get(
            "/api/v1/messages/by-idempotency", headers={**headers, "Idempotency-Key": "absent"}
        )
        assert before.status_code == 404
        assert (
            client.get(
                "/api/v1/messages/by-idempotency",
                headers={**headers, "Idempotency-Key": "same-command"},
            ).status_code
            == 200
        )
        assert (
            client.get(
                "/api/v1/messages/by-idempotency",
                headers={**headers, "X-API-Key": "wrong", "Idempotency-Key": "same-command"},
            ).status_code
            == 401
        )
    # Competing fresh commands cannot overspend the tenant quota.
    with SessionLocal.begin() as db:
        db.get(PricingPlan, plan_id).monthly_quota = 3
    with ThreadPoolExecutor(max_workers=2) as pool:
        limited = list(pool.map(submit, [("quota-one", "one"), ("quota-two", "two")]))
    assert sorted(status for status, _ in limited) == [202, 429]
    with SessionLocal() as db:
        assert (
            db.query(Message).count()
            == db.query(SmsDispatchJob).count()
            == db.query(SmsAcceptanceReceipt).count()
            == 3
        )
        assert all(
            row.state == "queued" and row.attempt_count == 0
            for row in db.scalars(select(SmsDispatchJob))
        )
        assert all(row.provider_message_id is None for row in db.scalars(select(Message)))
    # Authenticate and deduplicate callbacks through the actual ASGI API, then
    # prove bounded PostgreSQL inbound/outbox identifiers and local suppression.
    import tempfile
    import time
    from billing.models import Contact, InboundMessage, Outbox, PhoneNumber, SmsProviderEventInbox
    from billing.provider_events import process_event, signature
    from billing.sms_integration import SmsInboundBinding

    callback_path = "/internal/v1/provider-events/jasmin"
    signing_secret = (uuid.uuid4().hex + uuid.uuid4().hex).encode()
    with tempfile.TemporaryDirectory() as temporary:
        key_file = Path(temporary) / "fixture-signing-key"
        key_file.write_bytes(signing_secret)
        key_file.chmod(0o600)
        os.environ["TELNEXA_PROVIDER_EVENT_HMAC_SECRET_FILE"] = str(key_file)
        with SessionLocal.begin() as db:
            message = db.get(Message, saved_id)
            message.provider_message_id = "synthetic-dlr-id"
            message.status = "submitted"
            number = PhoneNumber(
                tenant_id=tenant_id, account_id=account_id, number="+498765432", status="active"
            )
            db.add(number)
            db.flush()
            db.add(
                SmsInboundBinding(
                    source_key_id="issue29-synthetic",
                    destination=number.number,
                    number_id=number.id,
                    enabled=True,
                )
            )

        def callback(kind, event_id, data):
            raw = json.dumps(
                {"event": kind, "data": data}, sort_keys=True, separators=(",", ":")
            ).encode()
            timestamp = str(int(time.time()))
            header = {
                "Content-Type": "application/json",
                "X-Signature-Version": "v2",
                "X-Key-ID": "issue29-synthetic",
                "X-Telnexa-Event-Id": event_id,
                "X-Telnexa-Timestamp": timestamp,
                "X-Telnexa-Signature": "sha256="
                + signature(
                    signing_secret,
                    "POST",
                    callback_path,
                    timestamp,
                    event_id,
                    raw,
                    "issue29-synthetic",
                ),
            }
            with TestClient(app) as client:
                return client.post(callback_path, headers=header, content=raw)

        with ThreadPoolExecutor(max_workers=4) as pool:
            dlrs = list(
                pool.map(
                    lambda _: callback(
                        "dlr", "d" * 64, {"id": "synthetic-dlr-id", "message_status": "DELIVRD"}
                    ),
                    range(4),
                )
            )
        assert all(response.status_code == 202 for response in dlrs)
        assert sum(not response.json()["duplicate"] for response in dlrs) == 1
        assert (
            callback(
                "dlr", "d" * 64, {"id": "synthetic-dlr-id", "message_status": "FAILED"}
            ).status_code
            == 409
        )
        assert (
            callback(
                "inbound",
                "e" * 64,
                {
                    "id": "synthetic-mo-id",
                    "from": body["destination"],
                    "to": "+498765432",
                    "content": "STOP",
                },
            ).status_code
            == 202
        )
        with SessionLocal.begin() as db:
            for row in db.scalars(
                select(SmsProviderEventInbox).where(SmsProviderEventInbox.state == "pending")
            ):
                process_event(db, row)
                process_event(db, row)
        with SessionLocal() as db:
            assert db.get(Message, saved_id).status == "delivered"
            assert db.query(InboundMessage).count() == 1
            assert (
                db.scalar(select(Contact).where(Contact.tenant_id == tenant_id)).consent_status
                == "opted_out"
            )
            mo = db.scalar(select(Outbox).where(Outbox.event_type == "sms.opted_out"))
            assert mo.state == "pending" and len(mo.correlation_id) == 36
            assert mo.envelope["payload"]["inbound_message_id"]
        assert submit(("suppressed-new", "not sent"))[0] == 409
        assert submit(("same-command", "same payload"))[1] == results[0][1]
    evidence = {
        "schema_version": "telnexa.issue29.source-certification.v1",
        "source_sha": sha,
        "disposable_postgresql": True,
        "concurrent_exact_replay": "PASS",
        "concurrent_altered_replay": "PASS",
        "atomic_quota": "PASS",
        "immutable_receipt": "PASS",
        "non_submitting_get_readback": "PASS",
        "concurrent_callback_deduplication": "PASS",
        "signed_dlr_and_local_stop": "PASS",
        "provider_submissions": 0,
        "external_effects": 0,
        "staging_runtime_certified": False,
        "production_certified": False,
    }
    destination = Path("issue29-evidence.json")
    destination.write_text(json.dumps(evidence, sort_keys=True, indent=2) + "\n")
    print("ISSUE29_DISPOSABLE_POSTGRES=PASS; PROVIDER_SUBMISSIONS=0; RUNTIME_CERTIFIED=NO")


if __name__ == "__main__":
    main()
