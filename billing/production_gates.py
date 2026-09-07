import os
from datetime import datetime, timezone

from sqlalchemy import select

from .models import SmsProductionCanaryGate


def production_enabled():
    return os.environ.get("TELNEXA_PRODUCTION_SMS_ENABLED", "false").lower() == "true"


def reserve_canary(db, tenant_id, sender, destination):
    if not production_enabled():
        return None
    gate = db.scalar(
        select(SmsProductionCanaryGate)
        .where(
            SmsProductionCanaryGate.enabled == True,
            SmsProductionCanaryGate.allowed_tenant == tenant_id,
            SmsProductionCanaryGate.allowed_sender == sender,
            SmsProductionCanaryGate.expires_at > datetime.now(timezone.utc),
        )
        .with_for_update()
    )
    if (
        not gate
        or destination not in gate.allowed_destinations
        or gate.reserved_count >= gate.max_submissions
    ):
        return None
    gate.reserved_count += 1
    gate.updated_at = datetime.now(timezone.utc)
    return gate


def validate_reserved_canary(db, gate_id, tenant_id, sender, destination, now=None):
    """Lock and revalidate the exact acceptance-time gate immediately before submission."""
    if not production_enabled() or not gate_id:
        return None
    now = now or datetime.now(timezone.utc)
    gate = db.scalar(
        select(SmsProductionCanaryGate)
        .where(SmsProductionCanaryGate.id == gate_id)
        .with_for_update()
    )
    expires_at = gate.expires_at if gate else None
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        not gate
        or not gate.enabled
        or expires_at <= now
        or gate.allowed_tenant != tenant_id
        or gate.allowed_sender != sender
        or destination not in gate.allowed_destinations
        or gate.claimed_count >= gate.max_submissions
    ):
        return None
    return gate
