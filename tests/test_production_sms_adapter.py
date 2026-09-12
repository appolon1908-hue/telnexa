import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx

from billing.adapters import JasminHttpAdapter, NormalizedSubmission, SubmissionOutcome
from billing.db import Base, SessionLocal, engine
from billing.dispatch import accept_message, claim_job, process_job
from billing.models import (
    BillingAccount,
    CountryPolicy,
    ConsentRecord,
    Contact,
    Message,
    MessageEvent,
    Outbox,
    PricingPlan,
    PhoneNumber,
    Provider,
    Rate,
    Route,
    SmsDispatchAttempt,
    SmsDispatchJob,
    SmsReconciliationCase,
    SmsProviderEventInbox,
    SmsProductionCanaryGate,
    SmsProductionAuthorization,
    SmsDeliveryPolicy,
    SmsSystemControl,
    SmsRouteDecision,
    Sender,
    Tenant,
    Usage,
    Wallet,
)
from billing.provider_capacity import acquire_provider_capacity, release_provider_capacity
from billing.provider_events import process_event, signature, verify_signature
from billing.dispatch_worker import adapter_for
from billing.adapters.errors import AdapterConfigurationError
import pytest
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
                country="DE",
                prefix="+49",
                currency="EUR",
                amount=Decimal(".02"),
                provider="jasmin",
                effective_from=now - timedelta(days=1),
            ),
            Rate(
                kind="sell",
                country="DE",
                prefix="+49",
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
        credential_reference="/run/secrets/jasmin_http",
        dlr_source_key_id="jasmin-primary",
    )
    db.add(provider)
    db.flush()
    db.add(Route(country="DE", prefix="+49", provider_id=provider.id, priority=10, enabled=True))
    db.add(
        CountryPolicy(
            country="DE",
            category="transactional",
            enabled=True,
            config={"allow_inbound_reopt_in": True},
        )
    )
    db.add(
        CountryPolicy(
            country="DE",
            category="marketing",
            enabled=True,
            config={"allow_inbound_reopt_in": True},
        )
    )
    db.add(Sender(tenant_id=tenant.id, sender="Telnexa", status="approved", countries=["DE"]))
    db.commit()
    return db, tenant, account


def test_acceptance_is_durable_and_not_simulated():
    db, tenant, account = seed_dispatch()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        db.query(Sender).one(),
        "hello",
        "transactional",
        "key-1",
        "corr-1",
        "a" * 64,
    )
    db.commit()
    assert message.status == "queued" and message.provider == "Private Jasmin"
    assert db.query(SmsDispatchJob).filter_by(message_id=message.id).count() == 1
    assert db.get(Wallet, db.query(Wallet).one().id).reserved == Decimal("0.040000")
    local_events = db.query(Outbox).all()
    assert local_events and all(item.state == "internal" for item in local_events)


def test_ambiguous_submission_never_invokes_backup_or_bills():
    db, tenant, account = seed_dispatch()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        db.query(Sender).one(),
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
        db.query(Sender).one(),
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


def test_middleware_outbox_uses_canonical_envelope_and_local_billing_stays_internal():
    db, tenant, account = seed_dispatch()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        db.query(Sender).one(),
        "hello",
        "transactional",
        "key-canonical-event",
        "corr-canonical-event",
        "d" * 64,
    )
    db.commit()
    job = claim_job(db, "test-worker")

    class Accepted:
        def submit(self, submission):
            from billing.adapters.base import SubmissionResult

            return SubmissionResult(
                SubmissionOutcome.ACCEPTED,
                provider_message_id="provider-canonical-1",
            )

    process_job(db, job, lambda provider: Accepted())
    db.commit()
    lifecycle = db.query(Outbox).filter_by(event_type="sms.submitted").one()
    envelope = lifecycle.envelope
    assert lifecycle.id == envelope["event_id"] == envelope["idempotency_key"]
    assert lifecycle.state == "pending"
    assert envelope["event_type"] == "codestra.sms.message.provider_accepted"
    assert envelope["source"] == "telnexa-gateway"
    assert envelope["tenant_id"] == tenant.id
    assert envelope["correlation_id"] == message.correlation_id
    assert envelope["causation_id"] == message.id
    assert envelope["payload"] == {
        "message_id": message.id,
        "status": "provider_accepted",
        "provider_message_id": "provider-canonical-1",
        "segments": 1,
        "message_idempotency_key": "key-canonical-event",
    }
    assert envelope["metadata"]["message_idempotency_key"] == "key-canonical-event"
    assert all(
        item.state == "internal"
        for item in db.query(Outbox).filter(Outbox.event_type.like("billing.%")).all()
    )


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

    def read_error(request):
        raise httpx.ReadError("response lost after request", request=request)

    adapter.transport = httpx.MockTransport(read_error)
    assert adapter.submit(submission).outcome == SubmissionOutcome.AMBIGUOUS


def test_generic_provider_rate_is_fallback_but_exact_rate_wins():
    db, tenant, account = seed_dispatch()
    exact = db.query(Rate).filter_by(kind="provider").one()
    exact.priority = 0
    db.add(
        Rate(
            kind="provider",
            country="DE",
            prefix="+49",
            currency="EUR",
            amount=Decimal(".03"),
            effective_from=datetime.now(timezone.utc) - timedelta(days=1),
        )
    )
    db.commit()
    first = accept_message(
        db,
        account.id,
        "+49123",
        db.query(Sender).one(),
        "one",
        "transactional",
        "exact-rate",
        "corr-exact",
        "1" * 64,
    )
    assert first.provider_rate_snapshot["id"] == exact.id
    db.delete(exact)
    db.commit()
    second = accept_message(
        db,
        account.id,
        "+49124",
        db.query(Sender).one(),
        "two",
        "transactional",
        "generic-rate",
        "corr-generic",
        "2" * 64,
    )
    assert second.provider_rate_snapshot["provider"] == "Private Jasmin"
    assert second.estimated_provider_cost == Decimal("0.030000")
    db.rollback()
    db.close()


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


def test_rate_and_route_policy_consensus_denies_zz_fallback():
    db, tenant, account = seed_dispatch()
    db.add_all(
        [
            Rate(
                kind="provider",
                country="ZZ",
                prefix="+",
                currency="EUR",
                amount=Decimal(".001"),
                provider="fallback",
                effective_from=datetime.now(timezone.utc) - timedelta(days=1),
            ),
            Rate(
                kind="sell",
                country="ZZ",
                prefix="+",
                currency="EUR",
                amount=Decimal(".002"),
                effective_from=datetime.now(timezone.utc) - timedelta(days=1),
            ),
        ]
    )
    message = accept_message(
        db,
        account.id,
        "+491234567",
        db.query(Sender).one(),
        "hello",
        "transactional",
        "consensus",
        "corr",
        "d" * 64,
    )
    db.flush()
    assert message.provider_rate_snapshot["country"] == "DE"
    assert message.provider_rate_snapshot["connector"] == "jasmin"
    assert message.sell_rate_snapshot["country"] == "DE"
    from billing.models import SmsRouteDecision

    decision = db.get(SmsRouteDecision, message.route_decision_id)
    assert decision.country == "DE" and decision.selected_provider_id == db.query(Provider).one().id

    db.rollback()
    db.query(CountryPolicy).filter_by(country="DE", category="transactional").one().enabled = False
    fallback = Provider(
        name="Fallback",
        connector="fallback",
        state="enabled",
        routing_enabled=True,
        adapter_type="jasmin_http",
        credential_reference="/run/secrets/jasmin_http",
        dlr_source_key_id="jasmin-fallback",
    )
    db.add(fallback)
    db.flush()
    db.add(Route(country="ZZ", prefix="+", provider_id=fallback.id, priority=100, enabled=True))
    db.add(CountryPolicy(country="ZZ", category="transactional", enabled=True))
    db.commit()
    with pytest.raises(ValueError, match="selected_route_country_policy_denied"):
        accept_message(
            db,
            account.id,
            "+491234567",
            db.query(Sender).one(),
            "hello",
            "transactional",
            "bypass",
            "corr",
            "e" * 64,
        )


def test_explicit_jasmin_secret_contract_and_safe_readback(tmp_path, monkeypatch):
    prefix = tmp_path / "jasmin_http"
    for suffix, value in (("_username", "user"), ("_password", "pass"), ("_dlr_token", "token")):
        (tmp_path / (prefix.name + suffix)).write_text(value)
    monkeypatch.setenv("TELNEXA_PROVIDER_SECRET_ROOT", str(tmp_path))
    provider = Provider(
        base_url="http://jasmin:1401",
        credential_reference=str(prefix),
        dlr_source_key_id="jasmin-test",
        connect_timeout_ms=100,
        request_timeout_ms=200,
    )
    assert adapter_for(provider).validate_credentials() == {
        "username": True,
        "password": True,
        "dlr_token": True,
    }
    provider.credential_reference = None
    with pytest.raises(AdapterConfigurationError, match="explicit_provider"):
        adapter_for(provider)


def test_shared_capacity_denies_twenty_worker_race():
    db, tenant, account = seed_dispatch()
    provider_id = db.query(Provider).one().id
    provider = db.get(Provider, provider_id)
    provider.max_inflight = 1
    provider.tps = 100
    db.commit()
    db.close()
    barrier = threading.Barrier(20)
    lock = threading.Lock()
    observed = {"active": 0, "maximum": 0}

    def worker():
        with SessionLocal.begin() as session:
            barrier.wait()
            if acquire_provider_capacity(session, provider_id):
                with lock:
                    observed["active"] += 1
                    observed["maximum"] = max(observed["maximum"], observed["active"])
                time.sleep(0.01)
                with lock:
                    observed["active"] -= 1
                release_provider_capacity(session, provider_id)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert observed["maximum"] <= 1


def test_shared_tps_denies_twenty_worker_race():
    db, tenant, account = seed_dispatch()
    provider_id = db.query(Provider).one().id
    provider = db.get(Provider, provider_id)
    provider.max_inflight = 20
    provider.tps = 3
    db.commit()
    db.close()
    barrier = threading.Barrier(20)
    lock = threading.Lock()
    successes = []

    def worker():
        with SessionLocal.begin() as session:
            barrier.wait()
            acquired = acquire_provider_capacity(session, provider_id)
            if acquired:
                release_provider_capacity(session, provider_id)
            with lock:
                successes.append(acquired)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sum(successes) == 3


def _mo_row(db, tenant, event_id, keyword, occurred_at):
    from billing.sms_integration import SmsInboundBinding

    if not db.get(SmsInboundBinding, ("key", "+498765432")):
        number = db.query(PhoneNumber).filter_by(tenant_id=tenant.id, number="+498765432").one()
        db.add(
            SmsInboundBinding(
                source_key_id="key", destination=number.number, number_id=number.id, enabled=True
            )
        )
        db.flush()
    row = SmsProviderEventInbox(
        source="jasmin",
        source_key_id="key",
        event_id=event_id,
        event_type="MO",
        provider_message_id=event_id,
        payload_hash=event_id.ljust(64, "0")[:64],
        normalized_payload={
            "event": "inbound",
            "data": {
                "id": event_id,
                "from": "+491234567",
                "to": "+498765432",
                "content": keyword,
                "tenant_id": "spoofed",
            },
        },
        occurred_at=occurred_at,
    )
    db.add(row)
    db.flush()
    process_event(db, row)
    db.commit()
    return row


def test_stop_start_unstop_consent_ledger_replay_and_tenant_isolation():
    db, tenant, account = seed_dispatch()
    db.add(
        PhoneNumber(
            tenant_id=tenant.id, account_id=account.id, number="+498765432", status="active"
        )
    )
    db.commit()
    t0 = datetime.now(timezone.utc)
    _mo_row(db, tenant, "stop-1", "STOP", t0)
    contact = db.query(Contact).filter_by(tenant_id=tenant.id, phone="+491234567").one()
    assert contact.consent_status == "opted_out"
    _mo_row(db, tenant, "old-start", "START", t0 - timedelta(seconds=1))
    db.refresh(contact)
    assert contact.consent_status == "opted_out"
    _mo_row(db, tenant, "start-1", "START", t0 + timedelta(seconds=1))
    db.refresh(contact)
    assert (
        contact.consent_status == "opted_in"
        and contact.opted_out_at is None
        and contact.suppression_reason is None
    )
    assert (
        db.query(ConsentRecord)
        .filter_by(tenant_id=tenant.id, phone=contact.phone, action="opt_in")
        .count()
        == 1
    )
    _mo_row(db, tenant, "duplicate-start", "START", t0 + timedelta(seconds=2))
    assert (
        db.query(ConsentRecord)
        .filter_by(tenant_id=tenant.id, phone=contact.phone, action="opt_in")
        .count()
        == 1
    )
    _mo_row(db, tenant, "stop-2", "STOP", t0 + timedelta(seconds=3))
    _mo_row(db, tenant, "unstop-1", "UNSTOP", t0 + timedelta(seconds=4))
    db.refresh(contact)
    assert contact.consent_status == "opted_in"
    assert (
        db.query(ConsentRecord)
        .filter_by(tenant_id=tenant.id, phone=contact.phone, action="opt_in")
        .count()
        == 2
    )
    assert all(row.tenant_id == tenant.id for row in db.query(SmsProviderEventInbox).all())


def test_closed_canary_gate_stops_queued_job_before_adapter(monkeypatch):
    db, tenant, account = seed_dispatch()
    provider = db.query(Provider).one()
    provider.environment = "production"
    provider.capabilities = {**provider.capabilities, "carrier": "didww"}
    release_sha = "a" * 40
    now = datetime.now(timezone.utc)
    gate = SmsProductionCanaryGate(
        gate_key="review-gate",
        stage="SINGLE_DESTINATION",
        allowed_tenant=tenant.id,
        allowed_sender="Telnexa",
        allowed_destinations=["+491234567"],
        max_submissions=1,
        reserved_count=1,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        approval_reference="review",
        approved_by="reviewer",
        enabled=True,
    )
    db.add(gate)
    db.flush()
    authorization = SmsProductionAuthorization(
        change_id="review-canary-change",
        idempotency_key="review-canary-authorization",
        request_sha256="9" * 64,
        tenant_id=tenant.id,
        environment="production",
        production_owner="owner",
        approved_senders=["Telnexa"],
        approved_destinations=["+491234567"],
        approved_categories=["transactional"],
        per_minute_segments=1,
        per_hour_segments=1,
        per_day_segments=1,
        provider_id=provider.id,
        billing_account_id=account.id,
        max_total_spend_minor=200,
        spending_currency="USD",
        account_grain="billing_account",
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(minutes=5),
        monitoring_owner="monitor",
        escalation_owner="escalation",
        rollback_owner="rollback",
        kill_switch_procedure="set production flag false",
        approved_release_sha=release_sha,
        approved_by="reviewer",
        authorization_timestamp=now,
        review_at=now + timedelta(minutes=5),
        reason="bounded canary dispatch test",
    )
    db.add(authorization)
    db.flush()
    policy = SmsDeliveryPolicy(
        tenant_id=tenant.id,
        environment="production",
        policy_version=1,
        enabled=True,
        mode="TRANSACTIONAL_CANARY",
        authorization_id=authorization.id,
        authorization_change_id=authorization.change_id,
        approved_senders=authorization.approved_senders,
        approved_destinations=authorization.approved_destinations,
        transaction_categories=authorization.approved_categories,
        per_minute_segments=1,
        per_hour_segments=1,
        per_day_segments=1,
        provider_id=authorization.provider_id,
        billing_account_id=account.id,
        max_total_spend_minor=200,
        spending_currency="USD",
        account_grain="billing_account",
        valid_from=authorization.valid_from,
        valid_until=authorization.valid_until,
        approved_by="reviewer",
        activated_by="operator",
        system_kill_switch=False,
        tenant_kill_switch=False,
        sender_kill_switches=[],
        reason="bounded canary dispatch test",
    )
    db.add(policy)
    db.add(
        SmsSystemControl(
            environment="production",
            control_version=1,
            kill_switch=False,
            reason="test canary system control explicitly opened",
            actor="test-operator",
        )
    )
    db.flush()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        db.query(Sender).one(),
        "hello",
        "transactional",
        "closed-gate",
        "corr-gate",
        "3" * 64,
        canary_gate_id=gate.id,
        production_policy_id=policy.id,
    )
    db.commit()
    job = claim_job(db, "gate-worker")
    gate.enabled = False
    db.commit()
    monkeypatch.setenv("TELNEXA_PRODUCTION_SMS_ENABLED", "true")
    monkeypatch.setenv("SOURCE_SHA", release_sha)
    calls = []
    process_job(db, job, lambda provider: calls.append(provider) or None)
    db.commit()
    assert calls == []
    assert db.get(Message, message.id).status == "rejected"
    assert job.last_error_code == "canary_gate_denied"


def test_dlr_is_correlated_by_authenticated_provider_identity():
    db, tenant, account = seed_dispatch()
    sender = db.query(Sender).one()
    first = accept_message(
        db,
        account.id,
        "+491231",
        sender,
        "first",
        "transactional",
        "provider-one",
        "corr-one",
        "4" * 64,
    )
    second = accept_message(
        db,
        account.id,
        "+491232",
        sender,
        "second",
        "transactional",
        "provider-two",
        "corr-two",
        "5" * 64,
    )
    provider_two = Provider(
        name="Private Jasmin Two",
        connector="jasmin-two",
        state="enabled",
        routing_enabled=True,
        adapter_type="jasmin_http",
        credential_reference="/run/secrets/jasmin_http_two",
        dlr_source_key_id="jasmin-secondary",
    )
    db.add(provider_two)
    db.flush()
    second.provider = provider_two.name
    db.get(SmsRouteDecision, second.route_decision_id).selected_provider_id = provider_two.id
    for index, message in enumerate((first, second), start=1):
        message.provider_message_id = "provider-local-id"
        transition(db, message, "dispatching", f"correlation-dispatch-{index}")
        transition(db, message, "submitted", f"correlation-submit-{index}")
    row = SmsProviderEventInbox(
        source="jasmin",
        source_key_id="jasmin-primary",
        event_id="dlr-composite",
        event_type="DLR",
        provider_message_id="provider-local-id",
        payload_hash="6" * 64,
        normalized_payload={
            "event": "dlr",
            "data": {"id": "provider-local-id", "status": "DELIVRD"},
        },
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(row)
    db.flush()
    process_event(db, row)
    db.commit()
    assert first.status == "delivered"
    assert second.status == "submitted"
    assert row.message_id == first.id and row.tenant_id == tenant.id


def test_dlr_downstream_idempotency_includes_authenticated_provider_key():
    db, tenant, account = seed_dispatch()
    sender = db.query(Sender).one()
    first = accept_message(
        db,
        account.id,
        "+491231",
        sender,
        "first",
        "transactional",
        "provider-key-one",
        "corr-key-one",
        "7" * 64,
    )
    second = accept_message(
        db,
        account.id,
        "+491232",
        sender,
        "second",
        "transactional",
        "provider-key-two",
        "corr-key-two",
        "8" * 64,
    )
    provider_two = Provider(
        name="Private Jasmin Two",
        connector="jasmin-two",
        state="enabled",
        routing_enabled=True,
        adapter_type="jasmin_http",
        credential_reference="/run/secrets/jasmin_http_two",
        dlr_source_key_id="jasmin-secondary",
    )
    db.add(provider_two)
    db.flush()
    second.provider = provider_two.name
    db.get(SmsRouteDecision, second.route_decision_id).selected_provider_id = provider_two.id
    for index, message in enumerate((first, second), start=1):
        message.provider_message_id = "shared-provider-local-id"
        transition(db, message, "dispatching", f"keyed-dispatch-{index}")
        transition(db, message, "submitted", f"keyed-submit-{index}")

    rows = []
    for source_key_id in ("jasmin-primary", "jasmin-secondary"):
        row = SmsProviderEventInbox(
            source="jasmin",
            source_key_id=source_key_id,
            event_id="shared-provider-event-id",
            event_type="DLR",
            provider_message_id="shared-provider-local-id",
            payload_hash=hashlib.sha256(source_key_id.encode()).hexdigest(),
            normalized_payload={
                "event": "dlr",
                "data": {"id": "shared-provider-local-id", "status": "DELIVRD"},
            },
            occurred_at=datetime.now(timezone.utc),
        )
        db.add(row)
        db.flush()
        process_event(db, row)
        rows.append(row)
    db.commit()

    assert first.status == second.status == "delivered"
    assert [row.message_id for row in rows] == [first.id, second.id]
    expected_identities = {
        hashlib.sha256(f"jasmin\0{source_key_id}\0shared-provider-event-id".encode()).hexdigest()
        for source_key_id in ("jasmin-primary", "jasmin-secondary")
    }
    delivery_events = db.query(MessageEvent).filter(MessageEvent.status == "delivered").all()
    assert {event.external_event_id for event in delivery_events} == {
        f"provider:{identity}" for identity in expected_identities
    }
    delivered_outbox = db.query(Outbox).filter(Outbox.event_type == "sms.delivered").all()
    assert {item.idempotency_key for item in delivered_outbox} == {
        f"sms:delivered:{identity}" for identity in expected_identities
    }
