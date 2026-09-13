import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import (
    String,
    DateTime,
    ForeignKey,
    Numeric,
    Integer,
    Boolean,
    JSON,
    Text,
    UniqueConstraint,
    CheckConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(timezone.utc)


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("pricing_plans.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PricingPlan(Base):
    __tablename__ = "pricing_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    billing_type: Mapped[str] = mapped_column(String(20), default="prepaid")
    markup_percent: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    http_tps: Mapped[int] = mapped_column(Integer, default=1)
    smpp_tps: Mapped[int] = mapped_column(Integer, default=1)
    monthly_quota: Mapped[int] = mapped_column(Integer, default=1000)
    max_api_keys: Mapped[int] = mapped_column(Integer, default=2)
    max_smpp_binds: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class BillingAccount(Base):
    __tablename__ = "billing_accounts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="EUR")
    billing_type: Mapped[str] = mapped_column(String(20), default="prepaid")
    credit_limit: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    low_balance_threshold: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("10"))
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    tax_metadata: Mapped[dict] = mapped_column(JSON, default=dict)


class Wallet(Base):
    __tablename__ = "wallets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id"), index=True)
    billing_account_id: Mapped[str] = mapped_column(ForeignKey("billing_accounts.id"), unique=True)
    currency: Mapped[str] = mapped_column(String(3))
    available: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    reserved: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    pending: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=Decimal("0"))
    version: Mapped[int] = mapped_column(Integer, default=0)


class LedgerEntry(Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        CheckConstraint("amount >= 0"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    billing_account_id: Mapped[str] = mapped_column(String(36))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    direction: Mapped[str] = mapped_column(String(6))
    type: Mapped[str] = mapped_column(String(40))
    reference_type: Mapped[str] = mapped_column(String(40))
    reference_id: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(180))
    actor: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class Reservation(Base):
    __tablename__ = "balance_reservations"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    billing_account_id: Mapped[str] = mapped_column(String(36))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(20), default="active")
    idempotency_key: Mapped[str] = mapped_column(String(180))
    message_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Rate(Base):
    __tablename__ = "rates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(20))
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    plan_id: Mapped[str | None] = mapped_column(String(36))
    provider: Mapped[str | None] = mapped_column(String(80))
    connector: Mapped[str | None] = mapped_column(String(80))
    country: Mapped[str] = mapped_column(String(2))
    network: Mapped[str | None] = mapped_column(String(20))
    prefix: Mapped[str] = mapped_column(String(32))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("tenant_id", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180))
    request_hash: Mapped[str] = mapped_column(String(64))
    correlation_id: Mapped[str] = mapped_column(String(36))
    destination: Mapped[str] = mapped_column(String(32))
    sender: Mapped[str] = mapped_column(String(20))
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[str | None] = mapped_column(String(5000))
    encoding: Mapped[str] = mapped_column(String(10))
    character_count: Mapped[int] = mapped_column(Integer)
    segments: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(80))
    provider_message_id: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    provider_rate_snapshot: Mapped[dict] = mapped_column(JSON)
    sell_rate_snapshot: Mapped[dict] = mapped_column(JSON)
    estimated_provider_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    estimated_sell_amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    actual_provider_cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    actual_sell_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    reservation_id: Mapped[str | None] = mapped_column(String(36))
    route_decision_id: Mapped[str | None] = mapped_column(String(36), index=True)
    dispatch_job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(80))
    submission_certainty: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Usage(Base):
    __tablename__ = "usage_records"
    __table_args__ = (UniqueConstraint("tenant_id", "message_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    message_id: Mapped[str] = mapped_column(String(36))
    country: Mapped[str] = mapped_column(String(2))
    provider: Mapped[str] = mapped_column(String(80))
    sender: Mapped[str] = mapped_column(String(20))
    segments: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(30))
    revenue: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    cost: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (UniqueConstraint("tenant_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    number: Mapped[str] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(String(3))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    issue_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    subtotal: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    tax: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    credits: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    amount_paid: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    status: Mapped[str] = mapped_column(String(30), default="draft")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class InvoiceLine(Base):
    __tablename__ = "invoice_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    invoice_id: Mapped[str] = mapped_column(ForeignKey("invoices.id"), index=True)
    description: Mapped[str] = mapped_column(String(255))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (UniqueConstraint("provider", "provider_reference"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    provider_reference: Mapped[str] = mapped_column(String(120))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(30))
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str | None] = mapped_column(String(36), index=True)
    prefix: Mapped[str] = mapped_column(String(16), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[str] = mapped_column(String(255), default="messages:write")
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ServiceAccount(Base):
    __tablename__ = "service_accounts"
    __table_args__ = (UniqueConstraint("tenant_id", "client_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(160))
    client_id: Mapped[str] = mapped_column(String(120), unique=True)
    secret_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Audit(Base):
    __tablename__ = "audit_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    target: Mapped[str] = mapped_column(String(120))
    correlation_id: Mapped[str] = mapped_column(String(36))
    before: Mapped[dict] = mapped_column(JSON, default=dict)
    after: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Outbox(Base):
    __tablename__ = "billing_outbox"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    correlation_id: Mapped[str] = mapped_column(String(36))
    envelope: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


# Product/control-plane records intentionally carry tenant_id directly.  API queries must
# always include it; the PostgreSQL migration additionally installs row-level security.
class TeamMember(Base):
    __tablename__ = "team_members"
    __table_args__ = (UniqueConstraint("tenant_id", "email"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    email: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(40), default="tenant_user")
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    mfa_secret_encrypted: Mapped[str | None] = mapped_column(String(512))
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="invited")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Sender(Base):
    __tablename__ = "senders"
    __table_args__ = (UniqueConstraint("tenant_id", "sender"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    sender: Mapped[str] = mapped_column(String(20))
    type: Mapped[str] = mapped_column(String(20), default="alphanumeric")
    status: Mapped[str] = mapped_column(String(20), default="requested")
    countries: Mapped[list] = mapped_column(JSON, default=list)
    rejection_reason: Mapped[str | None] = mapped_column(String(500))
    metadata_json: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (UniqueConstraint("tenant_id", "phone"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    phone: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(160))
    consent_status: Mapped[str] = mapped_column(String(24), default="unknown")
    consent_source: Mapped[str | None] = mapped_column(String(120))
    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_reference: Mapped[str | None] = mapped_column(String(255))
    purpose: Mapped[str | None] = mapped_column(String(80))
    opted_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suppression_reason: Mapped[str | None] = mapped_column(String(255))
    custom_fields: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(String(5000))
    category: Mapped[str] = mapped_column(String(30), default="transactional")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(160))
    template_id: Mapped[str | None] = mapped_column(String(36))
    sender_id: Mapped[str | None] = mapped_column(String(36))
    category: Mapped[str] = mapped_column(String(30), default="marketing")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(80), default="UTC")
    recipient_count: Mapped[int] = mapped_column(Integer, default=0)
    estimated_segments: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    approval_required: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Webhook(Base):
    __tablename__ = "webhooks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    url: Mapped[str] = mapped_column(String(500))
    events: Mapped[list] = mapped_column(JSON, default=list)
    secret_hash: Mapped[str] = mapped_column(String(255))
    secret_ciphertext: Mapped[str] = mapped_column(String(1000))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (UniqueConstraint("webhook_id", "event_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    webhook_id: Mapped[str] = mapped_column(String(36), index=True)
    event_id: Mapped[str] = mapped_column(String(36))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    response_code: Mapped[int | None] = mapped_column(Integer)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Provider(Base):
    __tablename__ = "providers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    connector: Mapped[str] = mapped_column(String(80), unique=True)
    state: Mapped[str] = mapped_column(String(20), default="disabled")
    health_score: Mapped[int] = mapped_column(Integer, default=100)
    circuit_state: Mapped[str] = mapped_column(String(20), default="closed")
    tps: Mapped[int] = mapped_column(Integer, default=1)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    adapter_type: Mapped[str] = mapped_column(String(40), default="simulator")
    credential_reference: Mapped[str | None] = mapped_column(String(255))
    dlr_source_key_id: Mapped[str | None] = mapped_column(String(120), unique=True)
    environment: Mapped[str] = mapped_column(String(30), default="simulator")
    base_url: Mapped[str | None] = mapped_column(String(500))
    routing_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    max_inflight: Mapped[int] = mapped_column(Integer, default=1)
    connect_timeout_ms: Mapped[int] = mapped_column(Integer, default=1000)
    request_timeout_ms: Mapped[int] = mapped_column(Integer, default=5000)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_streak: Mapped[int] = mapped_column(Integer, default=0)
    inflight_count: Mapped[int] = mapped_column(Integer, default=0)
    tps_window_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tps_window_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Route(Base):
    __tablename__ = "routes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    country: Mapped[str] = mapped_column(String(2))
    prefix: Mapped[str] = mapped_column(String(32))
    sender_type: Mapped[str | None] = mapped_column(String(20))
    provider_id: Mapped[str] = mapped_column(String(36))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class MessageEvent(Base):
    __tablename__ = "message_events"
    __table_args__ = (UniqueConstraint("tenant_id", "external_event_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    message_id: Mapped[str] = mapped_column(String(36), index=True)
    external_event_id: Mapped[str] = mapped_column(String(120))
    type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))
    provider_response: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class InboundMessage(Base):
    __tablename__ = "inbound_messages"
    __table_args__ = (UniqueConstraint("provider", "provider_message_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    provider: Mapped[str] = mapped_column(String(80))
    provider_message_id: Mapped[str] = mapped_column(String(120))
    sender: Mapped[str] = mapped_column(String(32))
    destination: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(String(5000))
    contact_id: Mapped[str | None] = mapped_column(String(36))
    conversation_key: Mapped[str] = mapped_column(String(100), index=True)
    unread: Mapped[bool] = mapped_column(Boolean, default=True)
    assigned_to: Mapped[str | None] = mapped_column(String(36))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmppCredential(Base):
    __tablename__ = "smpp_credentials"
    __table_args__ = (
        CheckConstraint(
            "enabled = false",
            name="ck_smpp_credentials_runtime_provisioned_before_enable",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    system_id: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    bind_mode: Mapped[str] = mapped_column(String(16), default="transceiver")
    max_binds: Mapped[int] = mapped_column(Integer, default=1)
    tps: Mapped[int] = mapped_column(Integer, default=1)
    ip_allowlist: Mapped[list] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CommandIdempotency(Base):
    __tablename__ = "command_idempotency"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "caller_identity",
            "resource",
            "action",
            "api_version",
            "idempotency_key",
            name="uq_command_idempotency_identity",
        ),
        CheckConstraint(
            "(response_json IS NOT NULL AND response_ciphertext IS NULL) "
            "OR (response_json IS NULL AND response_ciphertext IS NOT NULL)",
            name="ck_command_idempotency_response",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    caller_identity: Mapped[str] = mapped_column(String(180))
    resource: Mapped[str] = mapped_column(String(160))
    action: Mapped[str] = mapped_column(String(80))
    api_version: Mapped[str] = mapped_column(String(20))
    idempotency_key: Mapped[str] = mapped_column(String(180))
    semantic_sha256: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    resource_id: Mapped[str | None] = mapped_column(String(36))
    response_json: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    response_ciphertext: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class CountryPolicy(Base):
    __tablename__ = "country_policies"
    __table_args__ = (UniqueConstraint("country", "category"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    country: Mapped[str] = mapped_column(String(2))
    category: Mapped[str] = mapped_column(String(30))
    stop_keywords: Mapped[list] = mapped_column(JSON, default=lambda: ["STOP"])
    help_keywords: Mapped[list] = mapped_column(JSON, default=lambda: ["HELP"])
    quiet_hours: Mapped[dict] = mapped_column(JSON, default=dict)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthToken(Base):
    __tablename__ = "auth_tokens"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    member_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    csrf_hash: Mapped[str | None] = mapped_column(String(64))
    user_agent_hash: Mapped[str | None] = mapped_column(String(64))
    ip_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    identity_hash: Mapped[str] = mapped_column(String(64), index=True)
    ip_hash: Mapped[str] = mapped_column(String(64), index=True)
    succeeded: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Subaccount(Base):
    __tablename__ = "subaccounts"
    __table_args__ = (UniqueConstraint("tenant_id", "name"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class PhoneNumber(Base):
    __tablename__ = "phone_numbers"
    __table_args__ = (UniqueConstraint("tenant_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    subaccount_id: Mapped[str | None] = mapped_column(String(36), index=True)
    number: Mapped[str] = mapped_column(String(32))
    capabilities: Mapped[list] = mapped_column(JSON, default=lambda: ["sms"])
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    account_id: Mapped[str | None] = mapped_column(String(36), index=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), index=True)
    phone: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(120))
    source_ip_hash: Mapped[str | None] = mapped_column(String(64))
    form_url: Mapped[str | None] = mapped_column(String(500))
    terms_version: Mapped[str | None] = mapped_column(String(80))
    privacy_version: Mapped[str | None] = mapped_column(String(80))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SmsDispatchJob(Base):
    __tablename__ = "sms_dispatch_jobs"
    __table_args__ = (CheckConstraint("attempt_count >= 0"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    message_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    state: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    lease_owner: Mapped[str | None] = mapped_column(String(120))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    selected_provider_id: Mapped[str | None] = mapped_column(String(36), index=True)
    route_decision_id: Mapped[str | None] = mapped_column(String(36))
    canary_gate_id: Mapped[str | None] = mapped_column(String(36), index=True)
    production_policy_id: Mapped[str | None] = mapped_column(String(36), index=True)
    last_error_class: Mapped[str | None] = mapped_column(String(80))
    last_error_code: Mapped[str | None] = mapped_column(String(120))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsDispatchAttempt(Base):
    __tablename__ = "sms_dispatch_attempts"
    __table_args__ = (UniqueConstraint("job_id", "attempt_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    message_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    provider_id: Mapped[str] = mapped_column(String(36), index=True)
    adapter_type: Mapped[str] = mapped_column(String(40))
    route_version: Mapped[int] = mapped_column(Integer)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(30))
    provider_message_id: Mapped[str | None] = mapped_column(String(120))
    http_status: Mapped[int | None] = mapped_column(Integer)
    provider_code: Mapped[str | None] = mapped_column(String(120))
    error_class: Mapped[str | None] = mapped_column(String(80))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    secret_free_evidence_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SmsRouteDecision(Base):
    __tablename__ = "sms_route_decisions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    message_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    destination_prefix: Mapped[str] = mapped_column(String(32))
    country: Mapped[str] = mapped_column(String(2))
    network: Mapped[str | None] = mapped_column(String(20))
    sender_id: Mapped[str | None] = mapped_column(String(36))
    country_policy_id: Mapped[str | None] = mapped_column(String(36))
    selected_provider_id: Mapped[str | None] = mapped_column(String(36), index=True)
    selected_route_id: Mapped[str | None] = mapped_column(String(36))
    route_version: Mapped[int | None] = mapped_column(Integer)
    candidate_summary: Mapped[list] = mapped_column(JSON, default=list)
    provider_rate_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    sell_rate_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsProviderEventInbox(Base):
    __tablename__ = "sms_provider_event_inbox"
    __table_args__ = (UniqueConstraint("source_key_id", "event_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    source: Mapped[str] = mapped_column(String(40))
    source_key_id: Mapped[str] = mapped_column(String(120))
    event_id: Mapped[str] = mapped_column(String(120))
    event_type: Mapped[str] = mapped_column(String(20))
    provider_message_id: Mapped[str | None] = mapped_column(String(120), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    normalized_payload: Mapped[dict] = mapped_column(JSON)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    state: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))


class SmsProviderEventAttempt(Base):
    __tablename__ = "sms_provider_event_attempts"
    __table_args__ = (UniqueConstraint("inbox_id", "attempt_number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    inbox_id: Mapped[str] = mapped_column(String(36), index=True)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(30))
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SmsReconciliationCase(Base):
    __tablename__ = "sms_reconciliation_cases"
    __table_args__ = (UniqueConstraint("case_type", "reference_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str | None] = mapped_column(String(36), index=True)
    message_id: Mapped[str | None] = mapped_column(String(36), index=True)
    case_type: Mapped[str] = mapped_column(String(50))
    reference_id: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(30), default="open", index=True)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    resolution: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsProductionCanaryGate(Base):
    __tablename__ = "sms_production_canary_gates"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    gate_key: Mapped[str] = mapped_column(String(120), unique=True)
    stage: Mapped[str] = mapped_column(String(40), default="SIMULATOR_ONLY")
    allowed_tenant: Mapped[str] = mapped_column(String(36), index=True)
    allowed_sender: Mapped[str] = mapped_column(String(20))
    allowed_destinations: Mapped[list] = mapped_column(JSON)
    max_submissions: Mapped[int] = mapped_column(Integer)
    reserved_count: Mapped[int] = mapped_column(Integer, default=0)
    claimed_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    approval_reference: Mapped[str] = mapped_column(String(255))
    approved_by: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsProductionAuthorization(Base):
    """Immutable owner authorization for one bounded production SMS scope."""

    __tablename__ = "sms_production_authorizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    change_id: Mapped[str] = mapped_column(String(120), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production")
    production_owner: Mapped[str] = mapped_column(String(120))
    approved_senders: Mapped[list] = mapped_column(JSON)
    approved_destinations: Mapped[list] = mapped_column(JSON)
    approved_categories: Mapped[list] = mapped_column(JSON)
    per_minute_segments: Mapped[int] = mapped_column(Integer)
    per_hour_segments: Mapped[int] = mapped_column(Integer)
    per_day_segments: Mapped[int] = mapped_column(Integer)
    provider_id: Mapped[str] = mapped_column(String(36), index=True)
    billing_account_id: Mapped[str] = mapped_column(String(36), index=True)
    max_total_spend_minor: Mapped[int] = mapped_column(Integer)
    spending_currency: Mapped[str] = mapped_column(String(3))
    account_grain: Mapped[str] = mapped_column(String(40))
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    monitoring_owner: Mapped[str] = mapped_column(String(120))
    escalation_owner: Mapped[str] = mapped_column(String(120))
    rollback_owner: Mapped[str] = mapped_column(String(120))
    kill_switch_procedure: Mapped[str] = mapped_column(String(500))
    approved_release_sha: Mapped[str] = mapped_column(String(64))
    approved_by: Mapped[str] = mapped_column(String(120))
    authorization_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    review_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsProductionAuthorizationRevocation(Base):
    """Append-only revocation; authorization rows are never mutated."""

    __tablename__ = "sms_production_authorization_revocations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    authorization_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), unique=True)
    revoked_by: Mapped[str] = mapped_column(String(120))
    correlation_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsDeliveryPolicy(Base):
    """Single current production-delivery policy for a tenant and environment."""

    __tablename__ = "sms_delivery_policies"
    __table_args__ = (UniqueConstraint("tenant_id", "environment"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True)
    environment: Mapped[str] = mapped_column(String(20), default="production")
    policy_version: Mapped[int] = mapped_column(Integer, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    mode: Mapped[str] = mapped_column(String(40), default="SAFE")
    authorization_id: Mapped[str | None] = mapped_column(String(36), index=True)
    authorization_change_id: Mapped[str | None] = mapped_column(String(120))
    approved_senders: Mapped[list] = mapped_column(JSON, default=list)
    approved_destinations: Mapped[list] = mapped_column(JSON, default=list)
    recipient_scope: Mapped[str] = mapped_column(String(40), default="exact_allowlist")
    transaction_categories: Mapped[list] = mapped_column(JSON, default=list)
    per_minute_segments: Mapped[int] = mapped_column(Integer, default=0)
    per_hour_segments: Mapped[int] = mapped_column(Integer, default=0)
    per_day_segments: Mapped[int] = mapped_column(Integer, default=0)
    provider_id: Mapped[str | None] = mapped_column(String(36), index=True)
    billing_account_id: Mapped[str | None] = mapped_column(String(36), index=True)
    max_total_spend_minor: Mapped[int] = mapped_column(Integer, default=0)
    spending_currency: Mapped[str | None] = mapped_column(String(3))
    account_grain: Mapped[str] = mapped_column(String(40), default="billing_account")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(120))
    activated_by: Mapped[str | None] = mapped_column(String(120))
    system_kill_switch: Mapped[bool] = mapped_column(Boolean, default=True)
    tenant_kill_switch: Mapped[bool] = mapped_column(Boolean, default=True)
    sender_kill_switches: Mapped[list] = mapped_column(JSON, default=list)
    reason: Mapped[str] = mapped_column(String(500), default="not authorized")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class SmsSystemControl(Base):
    """Global production dispatch kill switch; callbacks and evidence stay active."""

    __tablename__ = "sms_system_controls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uid)
    environment: Mapped[str] = mapped_column(String(20), unique=True, default="production")
    control_version: Mapped[int] = mapped_column(Integer, default=1)
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=True)
    reason: Mapped[str] = mapped_column(String(500), default="not authorized")
    actor: Mapped[str] = mapped_column(String(120), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
