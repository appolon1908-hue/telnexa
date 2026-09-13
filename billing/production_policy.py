"""Fail-closed post-canary SMS production authorization.

This module never enables the external dispatch worker. The deployment-level
``TELNEXA_PRODUCTION_SMS_ENABLED`` interlock must also be open. Policy checks are
performed at acceptance and again immediately before provider submission.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal

from sqlalchemy import func, select

from .models import (
    BillingAccount,
    Message,
    Provider,
    SmsDeliveryPolicy,
    SmsProductionAuthorization,
    SmsProductionAuthorizationRevocation,
    SmsProductionCanaryGate,
    SmsSystemControl,
)
from .production_gates import reserve_canary

PRODUCTION_MODES = frozenset(
    {"SAFE", "TRANSACTIONAL_CANARY", "TRANSACTIONAL_PRODUCTION", "CAMPAIGN_PRODUCTION"}
)
TRANSACTIONAL_CATEGORIES = frozenset({"transactional", "service"})
RELEASE_SHA = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")


class ProductionPolicyDenied(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DeliveryAuthority:
    policy_id: str
    canary_gate_id: str | None


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _canonical_list(value) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ProductionPolicyDenied("production_policy_invalid")
    if len(value) != len(set(value)):
        raise ProductionPolicyDenied("production_policy_invalid")
    return sorted(value)


def _authorization_for(db, policy: SmsDeliveryPolicy, now: datetime) -> SmsProductionAuthorization:
    if not policy.authorization_id:
        raise ProductionPolicyDenied("production_authorization_missing")
    authorization = db.get(SmsProductionAuthorization, policy.authorization_id)
    if authorization is None or authorization.tenant_id != policy.tenant_id:
        raise ProductionPolicyDenied("production_authorization_missing")
    revoked = db.scalar(
        select(SmsProductionAuthorizationRevocation.id).where(
            SmsProductionAuthorizationRevocation.authorization_id == authorization.id
        )
    )
    if revoked is not None:
        raise ProductionPolicyDenied("production_authorization_revoked")
    valid_from = _aware(authorization.valid_from)
    valid_until = _aware(authorization.valid_until)
    review_at = _aware(authorization.review_at)
    if (
        authorization.environment != "production"
        or valid_from is None
        or valid_until is None
        or review_at is None
        or not valid_from <= now < valid_until
        or now >= review_at
    ):
        raise ProductionPolicyDenied("production_authorization_expired")
    source_sha = os.environ.get("SOURCE_SHA", "")
    if not RELEASE_SHA.fullmatch(source_sha) or source_sha != authorization.approved_release_sha:
        raise ProductionPolicyDenied("production_release_not_authorized")
    if (
        policy.authorization_change_id != authorization.change_id
        or _canonical_list(policy.approved_senders)
        != _canonical_list(authorization.approved_senders)
        or _canonical_list(policy.approved_destinations)
        != _canonical_list(authorization.approved_destinations)
        or _canonical_list(policy.transaction_categories)
        != _canonical_list(authorization.approved_categories)
        or policy.per_minute_segments != authorization.per_minute_segments
        or policy.per_hour_segments != authorization.per_hour_segments
        or policy.per_day_segments != authorization.per_day_segments
        or policy.provider_id != authorization.provider_id
        or policy.billing_account_id != authorization.billing_account_id
        or policy.max_total_spend_minor != authorization.max_total_spend_minor
        or policy.spending_currency != authorization.spending_currency
        or policy.account_grain != authorization.account_grain
        or _aware(policy.valid_from) != valid_from
        or _aware(policy.valid_until) != valid_until
        or policy.approved_by != authorization.approved_by
    ):
        raise ProductionPolicyDenied("production_policy_authorization_mismatch")
    return authorization


def _validate_policy(
    db,
    policy: SmsDeliveryPolicy | None,
    *,
    sender: str,
    destination: str,
    category: str,
    provider_id: str | None,
    now: datetime,
) -> SmsDeliveryPolicy:
    if policy is None or not policy.enabled or policy.mode == "SAFE":
        raise ProductionPolicyDenied("production_policy_denied")
    if policy.mode not in PRODUCTION_MODES or policy.policy_version < 1:
        raise ProductionPolicyDenied("production_policy_invalid")
    if policy.environment != "production" or policy.recipient_scope != "exact_allowlist":
        raise ProductionPolicyDenied("production_policy_invalid")
    system = db.scalar(select(SmsSystemControl).where(SmsSystemControl.environment == "production"))
    if system is None or system.kill_switch:
        raise ProductionPolicyDenied("production_system_kill_switch_engaged")
    if policy.mode == "CAMPAIGN_PRODUCTION" or category not in TRANSACTIONAL_CATEGORIES:
        raise ProductionPolicyDenied("campaign_mode_not_certified")
    if policy.system_kill_switch or policy.tenant_kill_switch:
        raise ProductionPolicyDenied("production_kill_switch_engaged")
    if sender in _canonical_list(policy.sender_kill_switches):
        raise ProductionPolicyDenied("sender_kill_switch_engaged")
    if sender not in _canonical_list(policy.approved_senders):
        raise ProductionPolicyDenied("production_sender_denied")
    if destination not in _canonical_list(policy.approved_destinations):
        raise ProductionPolicyDenied("production_destination_denied")
    if category not in _canonical_list(policy.transaction_categories):
        raise ProductionPolicyDenied("production_category_denied")
    _authorization_for(db, policy, now)
    provider = db.get(Provider, policy.provider_id) if policy.provider_id else None
    if (
        provider is None
        or provider.state != "enabled"
        or not provider.routing_enabled
        or provider.circuit_state != "closed"
        or provider.adapter_type != "jasmin_http"
        or not provider.credential_reference
        or not provider.dlr_source_key_id
        or provider.environment != "production"
        or str(provider.capabilities.get("carrier", "")).casefold() != "didww"
    ):
        raise ProductionPolicyDenied("production_provider_unhealthy")
    if provider_id is not None and provider.id != provider_id:
        raise ProductionPolicyDenied("production_provider_mismatch")
    account = db.scalar(select(BillingAccount).where(BillingAccount.tenant_id == policy.tenant_id))
    if (
        account is None
        or policy.account_grain != "billing_account"
        or policy.billing_account_id != account.id
        or policy.spending_currency != "USD"
        or not 1 <= policy.max_total_spend_minor <= 200
    ):
        raise ProductionPolicyDenied("production_spend_policy_invalid")
    return policy


def _segments_since(db, tenant_id: str, since: datetime) -> int:
    value = db.scalar(
        select(func.coalesce(func.sum(Message.segments), 0)).where(
            Message.tenant_id == tenant_id,
            Message.created_at >= since,
        )
    )
    return int(value or 0)


def _total_spend_minor(
    db,
    *,
    tenant_id: str,
    since: datetime,
    currency: str,
    proposed_cost: Decimal,
    proposed_currency: str,
) -> int:
    if proposed_currency != currency or proposed_cost < 0:
        raise ProductionPolicyDenied("production_spend_currency_mismatch")
    total = Decimal(proposed_cost)
    rows = db.scalars(
        select(Message).where(
            Message.tenant_id == tenant_id,
            Message.created_at >= since,
        )
    ).all()
    for message in rows:
        if message.provider_rate_snapshot.get("currency") != currency:
            raise ProductionPolicyDenied("production_spend_currency_mismatch")
        total += Decimal(message.estimated_provider_cost)
    return int((total * 100).quantize(Decimal("1"), rounding=ROUND_CEILING))


def reserve_delivery_authority(
    db,
    *,
    tenant_id: str,
    sender: str,
    destination: str,
    category: str,
    segments: int,
    provider_id: str,
    country: str,
    provider_cost: Decimal,
    provider_currency: str,
    now: datetime | None = None,
) -> DeliveryAuthority:
    if type(segments) is not int or segments < 1:
        raise ProductionPolicyDenied("production_segment_count_invalid")
    if country == "US":
        raise ProductionPolicyDenied("usa_destinations_prohibited")
    now = now or datetime.now(timezone.utc)
    policy = db.scalar(
        select(SmsDeliveryPolicy)
        .where(
            SmsDeliveryPolicy.tenant_id == tenant_id,
            SmsDeliveryPolicy.environment == "production",
        )
        .with_for_update()
    )
    policy = _validate_policy(
        db,
        policy,
        sender=sender,
        destination=destination,
        category=category,
        provider_id=provider_id,
        now=now,
    )
    if policy.mode == "TRANSACTIONAL_CANARY":
        gate = reserve_canary(db, tenant_id, sender, destination)
        if gate is None:
            raise ProductionPolicyDenied("production_canary_gate_denied")
        return DeliveryAuthority(policy.id, gate.id)
    if policy.mode != "TRANSACTIONAL_PRODUCTION":
        raise ProductionPolicyDenied("production_policy_denied")
    limits = (
        (timedelta(minutes=1), policy.per_minute_segments, "production_minute_quota_exceeded"),
        (timedelta(hours=1), policy.per_hour_segments, "production_hour_quota_exceeded"),
        (timedelta(days=1), policy.per_day_segments, "production_day_quota_exceeded"),
    )
    for window, limit, code in limits:
        if type(limit) is not int or limit < 1:
            raise ProductionPolicyDenied("production_policy_invalid")
        if _segments_since(db, tenant_id, now - window) + segments > limit:
            raise ProductionPolicyDenied(code)
    if (
        _total_spend_minor(
            db,
            tenant_id=tenant_id,
            since=_aware(policy.valid_from) or now,
            currency=policy.spending_currency or "",
            proposed_cost=provider_cost,
            proposed_currency=provider_currency,
        )
        > policy.max_total_spend_minor
    ):
        raise ProductionPolicyDenied("production_spend_ceiling_exceeded")
    return DeliveryAuthority(policy.id, None)


def validate_reserved_policy(
    db,
    *,
    policy_id: str | None,
    tenant_id: str,
    sender: str,
    destination: str,
    category: str,
    provider_id: str,
    country: str,
    now: datetime | None = None,
) -> SmsDeliveryPolicy:
    if not policy_id:
        raise ProductionPolicyDenied("production_policy_denied")
    if country == "US":
        raise ProductionPolicyDenied("usa_destinations_prohibited")
    policy = db.scalar(
        select(SmsDeliveryPolicy).where(SmsDeliveryPolicy.id == policy_id).with_for_update()
    )
    if policy is not None and policy.tenant_id != tenant_id:
        raise ProductionPolicyDenied("production_policy_denied")
    return _validate_policy(
        db,
        policy,
        sender=sender,
        destination=destination,
        category=category,
        provider_id=provider_id,
        now=now or datetime.now(timezone.utc),
    )


def validate_production_readiness(db, now: datetime | None = None) -> dict[str, int | str]:
    """Validate that at least one exact tenant policy can authorize no-op inspection."""

    now = now or datetime.now(timezone.utc)
    policies = db.scalars(
        select(SmsDeliveryPolicy).where(
            SmsDeliveryPolicy.environment == "production",
            SmsDeliveryPolicy.enabled == True,
        )
    ).all()
    if not policies:
        raise ProductionPolicyDenied("production_policy_denied")
    validated = 0
    for policy in policies:
        senders = _canonical_list(policy.approved_senders)
        destinations = _canonical_list(policy.approved_destinations)
        categories = _canonical_list(policy.transaction_categories)
        if not senders or not destinations or not categories or not policy.provider_id:
            raise ProductionPolicyDenied("production_policy_invalid")
        _validate_policy(
            db,
            policy,
            sender=senders[0],
            destination=destinations[0],
            category=categories[0],
            provider_id=policy.provider_id,
            now=now,
        )
        if policy.mode == "TRANSACTIONAL_CANARY":
            gate = db.scalar(
                select(SmsProductionCanaryGate).where(
                    SmsProductionCanaryGate.enabled == True,
                    SmsProductionCanaryGate.allowed_tenant == policy.tenant_id,
                    SmsProductionCanaryGate.allowed_sender == senders[0],
                    SmsProductionCanaryGate.expires_at > now,
                )
            )
            if (
                gate is None
                or destinations[0] not in gate.allowed_destinations
                or gate.reserved_count >= gate.max_submissions
            ):
                raise ProductionPolicyDenied("production_canary_gate_denied")
        validated += 1
    return {"status": "ready", "active_production_policies": validated}
