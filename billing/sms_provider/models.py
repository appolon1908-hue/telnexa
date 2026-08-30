from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from billing.db import Base
from billing.models import now, uid


class SmsProviderOperation(Base):
    __tablename__ = "sms_provider_operations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        UniqueConstraint("tenant_id", "message_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    message_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    request_digest: Mapped[str] = mapped_column(String(64))
    requested_by: Mapped[str] = mapped_column(String(300))
    correlation_id: Mapped[str] = mapped_column(String(180), index=True)
    client_reference: Mapped[str] = mapped_column(String(120), index=True)
    billing_account_id: Mapped[str] = mapped_column(String(128), index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(128), index=True)
    destination: Mapped[str] = mapped_column(String(20))
    sender: Mapped[str] = mapped_column(String(20))
    content_ciphertext: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(30))
    encoding: Mapped[str] = mapped_column(String(10))
    character_count: Mapped[int] = mapped_column(Integer)
    segments: Mapped[int] = mapped_column(Integer)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state: Mapped[str] = mapped_column(String(40), default="pending")
    provider_status: Mapped[str | None] = mapped_column(String(80))
    provider_message_id: Mapped[str | None] = mapped_column(String(180), index=True)
    provider_id: Mapped[str] = mapped_column(String(36))
    provider_name: Mapped[str] = mapped_column(String(120))
    connector: Mapped[str] = mapped_column(String(120))
    route_id: Mapped[str] = mapped_column(String(36))
    country: Mapped[str] = mapped_column(String(2))
    reservation_id: Mapped[str | None] = mapped_column(String(36))
    estimated_provider_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    estimated_sell_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    actual_provider_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actual_sell_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    submission_attempts: Mapped[int] = mapped_column(Integer, default=0)
    reconciliation_attempts: Mapped[int] = mapped_column(Integer, default=0)
    transition_version: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error: Mapped[str | None] = mapped_column(String(500))
    provider_response: Mapped[dict] = mapped_column(JSON, default=dict)
    readback_evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    submission_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    provider_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    reconciliation_required_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SmsProviderEvent(Base):
    __tablename__ = "sms_provider_events"
    __table_args__ = (UniqueConstraint("tenant_id", "external_event_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    external_event_id: Mapped[str] = mapped_column(String(180))
    event_type: Mapped[str] = mapped_column(String(40))
    payload_digest: Mapped[str] = mapped_column(String(64))
    canonical_status: Mapped[str | None] = mapped_column(String(40))
    provider_status: Mapped[str | None] = mapped_column(String(80))
    provider_message_id: Mapped[str | None] = mapped_column(String(180), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsReconciliationRead(Base):
    __tablename__ = "sms_reconciliation_reads"
    __table_args__ = (UniqueConstraint("operation_id", "attempt"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    operation_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(40))
    provider_message_id: Mapped[str | None] = mapped_column(String(180))
    provider_status: Mapped[str | None] = mapped_column(String(80))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsCallbackOutbox(Base):
    __tablename__ = "sms_callback_outbox"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    event_id: Mapped[str] = mapped_column(String(180), unique=True)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    operation_id: Mapped[str | None] = mapped_column(String(36), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(60))
    target_path: Mapped[str] = mapped_column(String(180))
    timestamp: Mapped[str] = mapped_column(String(30))
    signature: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsOptOut(Base):
    __tablename__ = "sms_opt_outs"
    __table_args__ = (UniqueConstraint("tenant_id", "phone"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(128), index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(80))
    keyword: Mapped[str | None] = mapped_column(String(40))
    provider_message_id: Mapped[str | None] = mapped_column(String(180))
    opted_out_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    reconsented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
