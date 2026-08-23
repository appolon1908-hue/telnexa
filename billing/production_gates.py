import os
from datetime import datetime, timezone

from sqlalchemy import select

from .models import SmsProductionCanaryGate


def production_enabled():
    return os.environ.get("TELNEXA_PRODUCTION_SMS_ENABLED", "false").lower() == "true"


def reserve_canary(db, tenant_id, sender, destination):
    if not production_enabled():
        return False
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
        return False
    gate.reserved_count += 1
    gate.updated_at = datetime.now(timezone.utc)
    return True
