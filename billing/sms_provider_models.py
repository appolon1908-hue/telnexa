"""Durable SQLAlchemy records for the Telnexa SMS provider boundary."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .sms_provider_contracts import uid, utcnow


class SmsBase(DeclarativeBase):
    pass


class SmsOperation(SmsBase):
    __tablename__ = "sms_provider_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_sms_provider_idempotency"),
        UniqueConstraint("tenant_id", "middleware_message_id", name="uq_sms_provider_message"),
        CheckConstraint(
            "submission_attempts >= 0 AND submission_attempts <= 1",
            name="ck_sms_provider_one_submission",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    middleware_message_id: Mapped[str] = mapped_column(String(36), index=True)
    command_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    request_sha256: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(180), index=True)
    requested_by: Mapped[str] = mapped_column(String(300))
    destination: Mapped[str] = mapped_column(String(32))
    sender: Mapped[str] = mapped_column(String(20))
    content_sha256: Mapped[str] = mapped_column(String(64))
    encoding: Mapped[str] = mapped_column(String(10))
    characters: Mapped[int] = mapped_column(Integer)
    segments: Mapped[int] = mapped_column(Integer)
    category: Mapped[str] = mapped_column(String(20))
    client_reference: Mapped[str] = mapped_column(String(120), index=True)
    billing_account_id: Mapped[str | None] = mapped_column(String(128))
    campaign_id: Mapped[str | None] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(32), default="reserved", index=True)
    provider: Mapped[str] = mapped_column(String(80), default="jasmin")
    provider_reference: Mapped[str | None] = mapped_column(String(160), index=True)
    provider_status: Mapped[str | None] = mapped_column(String(80))
    submission_attempts: Mapped[int] = mapped_column(Integer, default=0)
    reconciliation_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SmsReservation(SmsBase):
    __tablename__ = "sms_provider_reservations"
    __table_args__ = (UniqueConstraint("operation_id", name="uq_sms_provider_reservation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    operation_id: Mapped[str] = mapped_column(String(36), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    state: Mapped[str] = mapped_column(String(20), default="reserved", index=True)
    reason: Mapped[str | None] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SmsProviderEvent(SmsBase):
    __tablename__ = "sms_provider_events"
    __table_args__ = (UniqueConstraint("tenant_id", "external_event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    external_event_id: Mapped[str] = mapped_column(String(256))
    event_type: Mapped[str] = mapped_column(String(120))
    canonical_status: Mapped[str] = mapped_column(String(32))
    provider_status: Mapped[str | None] = mapped_column(String(80))
    payload_sha256: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ignored_transition: Mapped[bool] = mapped_column(default=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SmsInbound(SmsBase):
    __tablename__ = "sms_provider_inbound"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "provider_message_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    provider: Mapped[str] = mapped_column(String(80), default="jasmin")
    provider_message_id: Mapped[str] = mapped_column(String(160))
    sender: Mapped[str] = mapped_column(String(32))
    destination: Mapped[str] = mapped_column(String(32))
    content_sha256: Mapped[str] = mapped_column(String(64))
    encoding: Mapped[str] = mapped_column(String(10))
    characters: Mapped[int] = mapped_column(Integer)
    segments: Mapped[int] = mapped_column(Integer)
    compliance_action: Mapped[str | None] = mapped_column(String(20))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SmsOptOut(SmsBase):
    __tablename__ = "sms_provider_opt_outs"
    __table_args__ = (UniqueConstraint("tenant_id", "phone", "scope_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    scope_key: Mapped[str] = mapped_column(String(160), default="tenant")
    reason: Mapped[str] = mapped_column(String(160))
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SmsCallbackOutbox(SmsBase):
    __tablename__ = "sms_provider_callback_outbox"
    __table_args__ = (UniqueConstraint("event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    event_id: Mapped[str] = mapped_column(String(256))
    event_type: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SmsReconciliationEvidence(SmsBase):
    __tablename__ = "sms_provider_reconciliation_evidence"
    __table_args__ = (UniqueConstraint("operation_id", "attempt_number"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    operation_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(32))
    provider_status: Mapped[str | None] = mapped_column(String(80))
    provider_reference: Mapped[str | None] = mapped_column(String(160))
    error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = [
    "SmsBase",
    "SmsOperation",
    "SmsReservation",
    "SmsProviderEvent",
    "SmsInbound",
    "SmsOptOut",
    "SmsCallbackOutbox",
    "SmsReconciliationEvidence",
]
