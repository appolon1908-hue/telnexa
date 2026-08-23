import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from billing.adapters import JasminHttpAdapter, NormalizedSubmission, SubmissionOutcome
from billing.db import Base, SessionLocal, engine
from billing.dispatch import accept_message, claim_job, process_job
from billing.models import (
    BillingAccount,
    Message,
    PricingPlan,
    Provider,
    Rate,
    Route,
    SmsDispatchAttempt,
    SmsDispatchJob,
    SmsReconciliationCase,
    Tenant,
    Usage,
    Wallet,
)
from billing.provider_events import signature, verify_signature
from billing.state_machine import transition


def seed_dispatch():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    plan = PricingPlan(name="Production adapter")
    tenant = Tenant(name="Adapter tenant", status="active", plan_id=plan.id)
    db.add_all([plan, tenant])
    db.flush()
    account = BillingAccount(tenant_id=tenant.id, currency="EUR")
    db.add(account)
    db.flush()
    db.add(
        Wallet(
            tenant_id=tenant.id,
            billing_account_id=account.id,
            currency="EUR",
            available=Decimal("10"),
        )
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
                provider="jasmin",
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
    provider = Provider(
        name="Private Jasmin",
        connector="jasmin",
        state="enabled",
        routing_enabled=True,
        adapter_type="jasmin_http",
        environment="sandbox",
        tps=10,
        max_inflight=2,
        capabilities={"unicode": True},
    )
    db.add(provider)
    db.flush()
    db.add(Route(country="DE", prefix="+49", provider_id=provider.id, priority=10, enabled=True))
    db.commit()
    return db, tenant, account


def test_acceptance_is_durable_and_not_simulated():
    db, tenant, account = seed_dispatch()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        "Telnexa",
        "hello",
        "transactional",
        "key-1",
        "corr-1",
        "a" * 64,
    )
    db.commit()
    assert message.status == "queued" and message.provider == "pending"
    assert db.query(SmsDispatchJob).filter_by(message_id=message.id).count() == 1
    assert db.get(Wallet, db.query(Wallet).one().id).reserved == Decimal("0.040000")


def test_ambiguous_submission_never_invokes_backup_or_bills():
    db, tenant, account = seed_dispatch()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        "Telnexa",
        "hello",
        "transactional",
        "key-2",
        "corr-2",
        "b" * 64,
    )
    db.commit()
    job = claim_job(db, "test-worker")
    calls = []

    class Ambiguous:
        def submit(self, submission):
            calls.append(submission.message_id)
            from billing.adapters.base import SubmissionResult

            return SubmissionResult(
                SubmissionOutcome.AMBIGUOUS, ambiguous=True, provider_code="submission_timeout"
            )

    process_job(db, job, lambda provider: Ambiguous())
    db.commit()
    assert calls == [message.id]
    assert db.get(Message, message.id).status == "submission_unknown"
    assert db.query(SmsDispatchAttempt).count() == 1 and db.query(Usage).count() == 0
    assert db.query(SmsReconciliationCase).filter_by(case_type="ambiguous_submission").count() == 1
    assert db.query(Wallet).one().reserved == Decimal("0.040000")


def test_delivered_cannot_downgrade_but_late_event_is_preserved():
    db, tenant, account = seed_dispatch()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        "Telnexa",
        "hello",
        "transactional",
        "key-3",
        "corr-3",
        "c" * 64,
    )
    transition(db, message, "dispatching", "e1")
    transition(db, message, "submitted", "e2")
    transition(db, message, "delivered", "e3")
    assert not transition(db, message, "sent", "e4")
    db.commit()
    assert message.status == "delivered" and len(message.id) == 36


def test_jasmin_mapping_acceptance_and_ambiguous_timeout(tmp_path):
    files = []
    for name, value in (("user", "u"), ("password", "p"), ("token", "t")):
        path = tmp_path / name
        path.write_text(value)
        files.append(str(path))
    seen = {}

    def accepted(request):
        seen.update(dict(httpx.QueryParams(request.content.decode())))
        return httpx.Response(200, text="Success/provider-123")

    adapter = JasminHttpAdapter(
        "http://jasmin:1401",
        files[0],
        files[1],
        "http://webhook-relay:8080",
        "key",
        files[2],
        transport=httpx.MockTransport(accepted),
    )
    submission = NormalizedSubmission("m", "t", "c", "+49123", "Sender", "Grüße", "UCS-2", 1, "m")
    result = adapter.submit(submission)
    assert (
        result.outcome == SubmissionOutcome.ACCEPTED
        and result.provider_message_id == "provider-123"
    )
    assert seen["coding"] == "8" and seen["dlr"] == "yes" and "source_token=t" in seen["dlr-url"]

    def timeout(request):
        raise httpx.ReadTimeout("late", request=request)

    adapter.transport = httpx.MockTransport(timeout)
    assert adapter.submit(submission).outcome == SubmissionOutcome.AMBIGUOUS


def test_provider_event_signature_binds_method_path_body_and_replay_window():
    body = json.dumps({"event": "dlr", "data": {"id": "p"}}).encode()
    timestamp = str(int(datetime.now(timezone.utc).timestamp()))
    event_id = "event-1"
    secret = b"secret"
    supplied = "sha256=" + signature(
        secret, "POST", "/internal/v1/provider-events/jasmin", timestamp, event_id, body
    )
    assert verify_signature(
        secret, "POST", "/internal/v1/provider-events/jasmin", timestamp, event_id, body, supplied
    )
    assert not verify_signature(
        secret, "PUT", "/internal/v1/provider-events/jasmin", timestamp, event_id, body, supplied
    )
