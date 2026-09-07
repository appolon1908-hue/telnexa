"""Durable acceptance receipts and bounded commercial SMS admission.

Receipt creation shares the message/reservation/dispatch transaction. No helper
in this module sends SMS, contacts a provider, or changes activation settings.
"""

import json
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import Campaign, Contact, Message, PricingPlan, Tenant, now


class SmsAcceptanceReceipt(Base):
    __tablename__ = "sms_acceptance_receipts"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    request_hash: Mapped[str] = mapped_column(String(64))
    response_text: Mapped[str] = mapped_column(Text)
    client_reference: Mapped[str | None] = mapped_column(String(120), index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), index=True)
    category: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsInboundBinding(Base):
    __tablename__ = "sms_inbound_bindings"
    source_key_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    destination: Mapped[str] = mapped_column(String(32), primary_key=True)
    number_id: Mapped[str] = mapped_column(ForeignKey("phone_numbers.id"))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


def bounded_reference(value, name, maximum):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or not value.isascii()
        or any(ord(char) < 33 or ord(char) > 126 for char in value)
    ):
        raise HTTPException(422, f"invalid_{name}")
    return value


def store_receipt(db, message, body, projection):
    receipt = SmsAcceptanceReceipt(
        message_id=message.id,
        tenant_id=message.tenant_id,
        idempotency_key=message.idempotency_key,
        request_hash=message.request_hash,
        response_text=json.dumps(projection, sort_keys=True, separators=(",", ":")),
        client_reference=body.client_reference,
        campaign_id=body.campaign_id,
        category=body.category,
    )
    db.add(receipt)
    db.flush()
    return json.loads(receipt.response_text)


def replay_receipt(db, message):
    receipt = db.get(SmsAcceptanceReceipt, message.id)
    if not receipt:
        # Never fabricate a historical acceptance from today's mutable status.
        # GET read-back remains available for legacy records, without submitting.
        raise HTTPException(409, "legacy_acceptance_requires_readback")
    if receipt.tenant_id != message.tenant_id or receipt.request_hash != message.request_hash:
        raise HTTPException(503, "acceptance_receipt_integrity_error")
    return json.loads(receipt.response_text)


def enforce_message_policy(db, tenant_id, destination, category, campaign_id):
    tenant = db.scalar(select(Tenant).where(Tenant.id == tenant_id).with_for_update())
    if not tenant or tenant.status != "active":
        raise HTTPException(403, "tenant_inactive")
    contact = db.scalar(
        select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone == destination)
    )
    if contact and (contact.opted_out_at or contact.consent_status == "opted_out"):
        raise HTTPException(409, "recipient_suppressed")
    if category == "marketing" and (not contact or contact.consent_status != "opted_in"):
        raise HTTPException(409, "marketing_consent_required_or_suppressed")
    if campaign_id:
        campaign = db.scalar(
            select(Campaign).where(Campaign.id == campaign_id, Campaign.tenant_id == tenant_id)
        )
        if not campaign or campaign.status != "approved":
            raise HTTPException(403, "campaign_not_approved")
        if campaign.category != category:
            raise HTTPException(403, "campaign_category_mismatch")
    return tenant


def enforce_admission(db, tenant_id, body):
    tenant = enforce_message_policy(
        db, tenant_id, body.destination, body.category, body.campaign_id
    )
    plan = db.get(PricingPlan, tenant.plan_id) if tenant.plan_id else None
    # Existing unassigned tenants remain bounded; explicit plans override the
    # compatibility ceiling. A zero/malformed configured limit is never unlimited.
    tps = plan.http_tps if plan else 100
    quota = plan.monthly_quota if plan else 1000
    if type(tps) is not int or type(quota) is not int or tps <= 0 or quota <= 0:
        raise HTTPException(403, "sms_plan_limit_denied")
    when = datetime.now(timezone.utc)
    recent = db.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.tenant_id == tenant_id, Message.created_at > when - timedelta(seconds=1))
    )
    if recent >= tps:
        raise HTTPException(429, "sms_rate_limit_exceeded", headers={"Retry-After": "1"})
    month = when.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    accepted = db.scalar(
        select(func.count())
        .select_from(Message)
        .where(Message.tenant_id == tenant_id, Message.created_at >= month)
    )
    if accepted >= quota:
        raise HTTPException(429, "sms_monthly_quota_exceeded")


def readback_by_key(db, tenant_id, key):
    bounded_reference(key, "idempotency_key", 180)
    message = db.scalar(
        select(Message).where(Message.tenant_id == tenant_id, Message.idempotency_key == key)
    )
    if not message:
        raise HTTPException(404, "submission_not_found")
    receipt = db.get(SmsAcceptanceReceipt, message.id)
    return {
        "contract_version": "telnexa.sms.readback.v1",
        "message_id": message.id,
        "tenant_id": message.tenant_id,
        "idempotency_key": message.idempotency_key,
        "request_hash": message.request_hash,
        "status": message.status,
        "provider_message_id": message.provider_message_id,
        "submission_certainty": message.submission_certainty,
        "correlation_id": message.correlation_id,
        "client_reference": receipt.client_reference if receipt else None,
        "campaign_id": receipt.campaign_id if receipt else None,
        "acceptance_snapshot_available": receipt is not None,
    }
