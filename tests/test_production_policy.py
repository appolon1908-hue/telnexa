from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from billing.dispatch import accept_message
from billing.models import (
    Message,
    Provider,
    Rate,
    Sender,
    SmsDispatchJob,
    SmsDeliveryPolicy,
    SmsProductionAuthorization,
    SmsProductionAuthorizationRevocation,
    SmsSystemControl,
    Wallet,
)
from billing.production_policy import ProductionPolicyDenied, reserve_delivery_authority
from tests.test_production_sms_adapter import seed_dispatch

RELEASE_SHA = "b" * 40


def authorized_policy(
    db,
    tenant,
    account,
    *,
    mode="TRANSACTIONAL_PRODUCTION",
    per_minute=2,
    per_hour=3,
    per_day=4,
    max_spend_minor=200,
):
    now = datetime.now(timezone.utc)
    provider = db.query(Provider).one()
    authorization = SmsProductionAuthorization(
        change_id="CHG-SMS-POLICY-1",
        idempotency_key="authorize-sms-policy-1",
        request_sha256="a" * 64,
        tenant_id=tenant.id,
        environment="production",
        production_owner="production-owner",
        approved_senders=["Telnexa"],
        approved_destinations=["+491234567"],
        approved_categories=["service", "transactional"],
        per_minute_segments=per_minute,
        per_hour_segments=per_hour,
        per_day_segments=per_day,
        provider_id=provider.id,
        billing_account_id=account.id,
        max_total_spend_minor=max_spend_minor,
        spending_currency="USD",
        account_grain="billing_account",
        valid_from=now - timedelta(minutes=1),
        valid_until=now + timedelta(hours=1),
        monitoring_owner="monitoring-owner",
        escalation_owner="escalation-owner",
        rollback_owner="rollback-owner",
        kill_switch_procedure="set TELNEXA_PRODUCTION_SMS_ENABLED false",
        approved_release_sha=RELEASE_SHA,
        approved_by="independent-reviewer",
        authorization_timestamp=now,
        review_at=now + timedelta(hours=1),
        reason="bounded transactional production certification",
    )
    db.add(authorization)
    db.flush()
    policy = SmsDeliveryPolicy(
        tenant_id=tenant.id,
        environment="production",
        policy_version=1,
        enabled=True,
        mode=mode,
        authorization_id=authorization.id,
        authorization_change_id=authorization.change_id,
        approved_senders=list(authorization.approved_senders),
        approved_destinations=list(authorization.approved_destinations),
        recipient_scope="exact_allowlist",
        transaction_categories=list(authorization.approved_categories),
        per_minute_segments=authorization.per_minute_segments,
        per_hour_segments=authorization.per_hour_segments,
        per_day_segments=authorization.per_day_segments,
        provider_id=provider.id,
        billing_account_id=account.id,
        max_total_spend_minor=authorization.max_total_spend_minor,
        spending_currency="USD",
        account_grain="billing_account",
        valid_from=authorization.valid_from,
        valid_until=authorization.valid_until,
        approved_by=authorization.approved_by,
        activated_by="production-operator",
        system_kill_switch=False,
        tenant_kill_switch=False,
        sender_kill_switches=[],
        reason="bounded transactional production certification",
    )
    db.add(policy)
    db.add(
        SmsSystemControl(
            environment="production",
            control_version=1,
            kill_switch=False,
            reason="test control explicitly opened",
            actor="test-operator",
        )
    )
    db.commit()
    return authorization, policy, provider


def production_context(monkeypatch):
    db, tenant, account = seed_dispatch()
    account.currency = "USD"
    db.query(Wallet).one().currency = "USD"
    for rate in db.query(Rate).all():
        rate.currency = "USD"
    provider = db.query(Provider).one()
    provider.environment = "production"
    provider.capabilities = {**provider.capabilities, "carrier": "didww"}
    db.commit()
    monkeypatch.setenv("SOURCE_SHA", RELEASE_SHA)
    return db, tenant, account


def reserve(db, tenant, account, provider, **updates):
    values = {
        "tenant_id": tenant.id,
        "sender": "Telnexa",
        "destination": "+491234567",
        "category": "transactional",
        "segments": 1,
        "provider_id": provider.id,
        "country": "DE",
        "provider_cost": Decimal("0.02"),
        "provider_currency": "USD",
    }
    values.update(updates)
    return reserve_delivery_authority(db, **values)


def test_transactional_production_requires_exact_authorized_scope(monkeypatch):
    db, tenant, account = production_context(monkeypatch)
    _authorization, policy, provider = authorized_policy(db, tenant, account)
    authority = reserve(db, tenant, account, provider)
    assert authority.policy_id == policy.id
    assert authority.canary_gate_id is None

    checks = (
        ({"sender": "Other"}, "production_sender_denied"),
        ({"destination": "+491234568"}, "production_destination_denied"),
        ({"category": "marketing"}, "campaign_mode_not_certified"),
        ({"provider_id": "other-provider"}, "production_provider_mismatch"),
        ({"country": "US"}, "usa_destinations_prohibited"),
    )
    for changes, code in checks:
        with pytest.raises(ProductionPolicyDenied, match=code):
            reserve(db, tenant, account, provider, **changes)


def test_policy_kill_switch_revocation_expiry_and_release_are_fail_closed(monkeypatch):
    db, tenant, account = production_context(monkeypatch)
    authorization, policy, provider = authorized_policy(db, tenant, account)

    policy.tenant_kill_switch = True
    db.commit()
    with pytest.raises(ProductionPolicyDenied, match="production_kill_switch_engaged"):
        reserve(db, tenant, account, provider)

    policy.tenant_kill_switch = False
    db.query(SmsSystemControl).one().kill_switch = True
    db.commit()
    with pytest.raises(ProductionPolicyDenied, match="production_system_kill_switch_engaged"):
        reserve(db, tenant, account, provider)

    db.query(SmsSystemControl).one().kill_switch = False
    db.add(
        SmsProductionAuthorizationRevocation(
            authorization_id=authorization.id,
            idempotency_key="revoke-policy-test",
            revoked_by="production-operator",
            correlation_id="11111111-1111-4111-8111-111111111111",
            reason="test revocation remains fail closed",
        )
    )
    db.commit()
    with pytest.raises(ProductionPolicyDenied, match="production_authorization_revoked"):
        reserve(db, tenant, account, provider)

    db, tenant, account = production_context(monkeypatch)
    _authorization, _policy, provider = authorized_policy(db, tenant, account)
    monkeypatch.setenv("SOURCE_SHA", "c" * 40)
    with pytest.raises(ProductionPolicyDenied, match="production_release_not_authorized"):
        reserve(db, tenant, account, provider)


def test_segment_and_two_dollar_spend_limits_are_enforced(monkeypatch):
    db, tenant, account = production_context(monkeypatch)
    _authorization, _policy, provider = authorized_policy(
        db, tenant, account, per_minute=1, per_hour=10, per_day=10
    )
    sender = db.query(Sender).one()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        sender,
        "first",
        "transactional",
        "policy-existing-message",
        "policy-existing-correlation",
        "d" * 64,
    )
    db.commit()
    assert isinstance(message, Message)

    with pytest.raises(ProductionPolicyDenied, match="production_minute_quota_exceeded"):
        reserve(db, tenant, account, provider)

    db, tenant, account = production_context(monkeypatch)
    _authorization, _policy, provider = authorized_policy(
        db,
        tenant,
        account,
        per_minute=10,
        per_hour=10,
        per_day=10,
        max_spend_minor=3,
    )
    sender = db.query(Sender).one()
    accept_message(
        db,
        account.id,
        "+491234567",
        sender,
        "first",
        "transactional",
        "policy-existing-spend",
        "policy-spend-correlation",
        "f" * 64,
    )
    db.commit()
    with pytest.raises(ProductionPolicyDenied, match="production_spend_ceiling_exceeded"):
        reserve(db, tenant, account, provider)


def test_live_acceptance_carries_policy_identity_to_dispatch(monkeypatch):
    db, tenant, account = production_context(monkeypatch)
    _authorization, policy, _provider = authorized_policy(db, tenant, account)
    monkeypatch.setenv("TELNEXA_PRODUCTION_SMS_ENABLED", "true")
    sender = db.query(Sender).one()
    message = accept_message(
        db,
        account.id,
        "+491234567",
        sender,
        "authorized",
        "transactional",
        "policy-authorized-message",
        "policy-authorized-correlation",
        "e" * 64,
    )
    db.commit()
    assert message.dispatch_job_id
    job = db.get(SmsDispatchJob, message.dispatch_job_id)
    assert job.production_policy_id == policy.id
    assert job.canary_gate_id is None


def test_live_acceptance_without_authorization_policy_is_denied(monkeypatch):
    db, tenant, account = production_context(monkeypatch)
    monkeypatch.setenv("TELNEXA_PRODUCTION_SMS_ENABLED", "true")
    with pytest.raises(ProductionPolicyDenied, match="production_policy_denied"):
        accept_message(
            db,
            account.id,
            "+491234567",
            db.query(Sender).one(),
            "not authorized",
            "transactional",
            "missing-production-policy",
            "missing-policy-correlation",
            "1" * 64,
        )
    assert db.query(Message).count() == 0
