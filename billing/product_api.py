"""Tenant-safe product APIs and simulator control plane.

The simulator endpoints never connect to a carrier.  They exercise MO, DLR,
compliance and failover state transitions using the same durable records used by
production adapters.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import select
from sqlalchemy import func
from sqlalchemy.orm import Session
from .db import session
from .models import (
    ApiKey,
    Audit,
    Campaign,
    Contact,
    CountryPolicy,
    InboundMessage,
    Message,
    MessageEvent,
    Provider,
    Rate,
    Route,
    Sender,
    Template,
    Usage,
    Webhook,
    WebhookDelivery,
    PhoneNumber,
    Subaccount,
    ConsentRecord,
    BillingAccount,
)
from .webhooks import encrypt_secret, validate_webhook_url
from .oidc import validate_bearer

router = APIRouter(prefix="/api/v1")


def auth(required="read"):
    def dependency(
        x_tenant_id: str = Header(...),
        x_api_key: str | None = Header(None),
        authorization: str | None = Header(None),
        db: Session = Depends(session),
    ):
        principal = validate_bearer(authorization, x_tenant_id, required)
        if principal:
            return x_tenant_id
        from .app import ph

        if not x_api_key:
            raise HTTPException(401, "authentication_required")
        row = db.scalar(
            select(ApiKey).where(
                ApiKey.tenant_id == x_tenant_id,
                ApiKey.prefix == x_api_key[:12],
                ApiKey.revoked == False,
            )
        )
        try:
            if not row:
                raise ValueError()
            ph.verify(row.secret_hash, x_api_key)
        except Exception:
            raise HTTPException(401, "invalid_api_key")
        scopes = set(row.scopes.split())
        aliases = {
            "read": {"read", "sms.read"},
            "messages:write": {"messages:write", "sms.send"},
            "bulk:write": {"bulk:write", "sms.bulk"},
            "webhooks:write": {"webhooks:write", "sms.webhook"},
            "senders:write": {"senders:write", "sms.send"},
            "contacts:write": {"contacts:write", "sms.send"},
            "campaigns:write": {"campaigns:write", "sms.bulk"},
        }
        allowed = aliases.get(required, {required})
        if (
            not scopes.intersection(allowed)
            and "admin" not in scopes
            and not (required == "read" and any(s.endswith(":read") for s in scopes))
        ):
            raise HTTPException(403, "insufficient_scope")
        return x_tenant_id

    return dependency


class ContactIn(BaseModel):
    phone: str = Field(pattern=r"^\+[1-9][0-9]{5,18}$")
    name: str | None = None
    consent_status: str = "unknown"
    consent_source: str | None = None
    consent_reference: str | None = None
    purpose: str | None = None
    form_url: str | None = None
    terms_version: str | None = None
    privacy_version: str | None = None
    custom_fields: dict = {}


class SenderIn(BaseModel):
    sender: str = Field(min_length=1, max_length=20)
    type: str = "alphanumeric"
    countries: list[str] = []
    metadata: dict = {}


class TemplateIn(BaseModel):
    name: str
    content: str = Field(min_length=1, max_length=5000)
    category: str = "transactional"


class CampaignIn(BaseModel):
    name: str
    template_id: str | None = None
    sender_id: str | None = None
    category: str = "marketing"
    recipient_count: int = Field(ge=0)
    scheduled_at: datetime | None = None
    timezone: str = "UTC"


class WebhookIn(BaseModel):
    url: HttpUrl
    events: list[str]


class MOIn(BaseModel):
    tenant_id: str
    provider_message_id: str
    sender: str
    destination: str
    content: str
    provider: str = "simulator"


class DLRIn(BaseModel):
    tenant_id: str
    message_id: str
    external_event_id: str
    status: str = Field(pattern="^(submitted|sent|delivered|failed|expired|undeliverable|unknown)$")
    provider_response: dict = {}


class OptOutIn(BaseModel):
    phone: str = Field(pattern=r"^\+[1-9][0-9]{5,18}$")
    scope: str = "tenant"
    campaign_id: str | None = None
    reason: str = "api_request"


@router.get("/messages/{message_id}")
def message(message_id: str, tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    row = db.scalar(select(Message).where(Message.id == message_id, Message.tenant_id == tenant_id))
    if not row:
        raise HTTPException(404, "message_not_found")
    return {
        "id": row.id,
        "status": row.status,
        "destination": row.destination,
        "sender": row.sender,
        "encoding": row.encoding,
        "segments": row.segments,
        "sell_cost": str(row.actual_sell_amount or row.estimated_sell_amount),
        "provider_cost": str(row.actual_provider_cost or row.estimated_provider_cost),
        "route": row.provider,
        "correlation_id": row.correlation_id,
    }


@router.get("/messages/{message_id}/events")
def events(message_id: str, tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    return {
        "items": [
            {
                "id": x.id,
                "type": x.type,
                "status": x.status,
                "occurred_at": x.occurred_at,
            }
            for x in db.scalars(
                select(MessageEvent)
                .where(
                    MessageEvent.tenant_id == tenant_id,
                    MessageEvent.message_id == message_id,
                )
                .order_by(MessageEvent.occurred_at)
            ).all()
        ]
    }


@router.get("/webhooks")
def webhooks(tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    return {
        "items": [
            {
                "id": x.id,
                "url": x.url,
                "events": x.events,
                "enabled": x.enabled,
                "created_at": x.created_at,
            }
            for x in db.scalars(select(Webhook).where(Webhook.tenant_id == tenant_id)).all()
        ]
    }


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(
    webhook_id: str,
    tenant_id: str = Depends(auth("webhooks:write")),
    db: Session = Depends(session),
):
    row = db.scalar(select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id))
    if not row:
        raise HTTPException(404, "webhook_not_found")
    db.delete(row)
    db.add(
        Audit(
            tenant_id=tenant_id,
            actor="api",
            action="webhook.deleted",
            target=webhook_id,
            correlation_id=str(uuid.uuid4()),
        )
    )
    db.commit()


@router.get("/numbers")
def numbers(tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    rows = db.scalars(select(PhoneNumber).where(PhoneNumber.tenant_id == tenant_id)).all()
    return {
        "items": [
            {
                "id": x.id,
                "number": x.number,
                "account_id": x.account_id,
                "subaccount_id": x.subaccount_id,
                "capabilities": x.capabilities,
                "status": x.status,
            }
            for x in rows
        ]
    }


@router.get("/subaccounts")
def subaccounts(tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    rows = db.scalars(select(Subaccount).where(Subaccount.tenant_id == tenant_id)).all()
    return {
        "items": [
            {"id": x.id, "account_id": x.account_id, "name": x.name, "status": x.status}
            for x in rows
        ]
    }


@router.post("/opt-outs", status_code=201)
def opt_out(
    body: OptOutIn,
    tenant_id: str = Depends(auth("contacts:write")),
    db: Session = Depends(session),
):
    row = db.scalar(
        select(Contact).where(Contact.tenant_id == tenant_id, Contact.phone == body.phone)
    ) or Contact(tenant_id=tenant_id, phone=body.phone)
    row.consent_status = "opted_out"
    row.opted_out_at = datetime.now(timezone.utc)
    row.suppression_reason = f"{body.scope}:{body.reason}"
    db.add(row)
    db.flush()
    account = db.scalar(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id))
    db.add(
        ConsentRecord(
            tenant_id=tenant_id,
            account_id=account.id if account else None,
            campaign_id=body.campaign_id,
            phone=body.phone,
            action="opt_out",
            source="api",
            metadata_json={"scope": body.scope, "reason": body.reason},
        )
    )
    db.add(
        Audit(
            tenant_id=tenant_id,
            actor="api",
            action="sms.opted_out",
            target=row.id,
            correlation_id=str(uuid.uuid4()),
            after={"scope": body.scope, "campaign_id": body.campaign_id},
        )
    )
    db.commit()
    return {"id": row.id, "suppressed": True}


@router.get("/opt-outs")
def opt_outs(tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    rows = db.scalars(
        select(Contact).where(Contact.tenant_id == tenant_id, Contact.opted_out_at != None)
    ).all()
    return {
        "items": [
            {
                "id": x.id,
                "phone": x.phone,
                "reason": x.suppression_reason,
                "opted_out_at": x.opted_out_at,
            }
            for x in rows
        ]
    }


@router.get("/rates")
def rates(tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    rows = db.scalars(
        select(Rate)
        .where((Rate.tenant_id == None) | (Rate.tenant_id == tenant_id))
        .order_by(Rate.country, Rate.prefix)
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "kind": r.kind,
                "country": r.country,
                "prefix": r.prefix,
                "currency": r.currency,
                "amount": str(r.amount),
                "effective_from": r.effective_from,
                "effective_to": r.effective_to,
            }
            for r in rows
        ]
    }


@router.post("/contacts", status_code=201)
def create_contact(
    body: ContactIn,
    request: Request,
    tenant_id: str = Depends(auth("contacts:write")),
    db: Session = Depends(session),
):
    if body.consent_status == "opted_in" and not body.consent_source:
        raise HTTPException(422, "consent_source_required")
    values = body.model_dump()
    form_url = values.pop("form_url")
    terms_version = values.pop("terms_version")
    privacy_version = values.pop("privacy_version")
    row = Contact(
        tenant_id=tenant_id,
        **values,
        consent_at=datetime.now(timezone.utc) if body.consent_status == "opted_in" else None,
    )
    db.add(row)
    db.flush()
    if body.consent_status == "opted_in":
        account = db.scalar(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id))
        ip = request.client.host if request.client else "unknown"
        db.add(
            ConsentRecord(
                tenant_id=tenant_id,
                account_id=account.id if account else None,
                phone=body.phone,
                action="opt_in",
                source=body.consent_source or "api",
                source_ip_hash=hashlib.sha256(ip.encode()).hexdigest(),
                form_url=form_url,
                terms_version=terms_version,
                privacy_version=privacy_version,
                metadata_json={
                    "reference": body.consent_reference,
                    "purpose": body.purpose,
                },
            )
        )
    db.commit()
    return {"id": row.id}


@router.get("/contacts")
def contacts(tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    return {
        "items": [
            {
                "id": c.id,
                "phone": c.phone,
                "name": c.name,
                "consent_status": c.consent_status,
                "opted_out": bool(c.opted_out_at),
            }
            for c in db.scalars(select(Contact).where(Contact.tenant_id == tenant_id)).all()
        ]
    }


@router.post("/senders", status_code=201)
def request_sender(
    body: SenderIn,
    tenant_id: str = Depends(auth("senders:write")),
    db: Session = Depends(session),
):
    row = Sender(
        tenant_id=tenant_id,
        sender=body.sender,
        type=body.type,
        countries=body.countries,
        metadata_json=body.metadata,
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "status": "requested", "carrier_approved": False}


@router.get("/senders")
def senders(tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    return {
        "items": [
            {
                "id": s.id,
                "sender": s.sender,
                "status": s.status,
                "countries": s.countries,
                "rejection_reason": s.rejection_reason,
            }
            for s in db.scalars(select(Sender).where(Sender.tenant_id == tenant_id)).all()
        ]
    }


@router.post("/templates", status_code=201)
def template(
    body: TemplateIn,
    tenant_id: str = Depends(auth("campaigns:write")),
    db: Session = Depends(session),
):
    row = Template(tenant_id=tenant_id, **body.model_dump())
    db.add(row)
    db.commit()
    return {"id": row.id, "status": row.status}


@router.post("/campaigns", status_code=201)
def campaign(
    body: CampaignIn,
    tenant_id: str = Depends(auth("campaigns:write")),
    db: Session = Depends(session),
):
    # Large/marketing campaigns remain approval-gated and are never auto-dispatched.
    row = Campaign(
        tenant_id=tenant_id,
        **body.model_dump(),
        approval_required=body.category == "marketing" or body.recipient_count > 100,
        status="pending_approval"
        if body.category == "marketing" or body.recipient_count > 100
        else "draft",
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "status": row.status, "dispatch_enabled": False}


@router.post("/webhooks", status_code=201)
def webhook(
    body: WebhookIn,
    tenant_id: str = Depends(auth("webhooks:write")),
    db: Session = Depends(session),
):
    try:
        url = validate_webhook_url(str(body.url))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    raw = "whsec_" + secrets.token_urlsafe(32)
    row = Webhook(
        tenant_id=tenant_id,
        url=url,
        events=body.events,
        secret_hash=hashlib.sha256(raw.encode()).hexdigest(),
        secret_ciphertext=encrypt_secret(raw),
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "secret": raw, "display_once": True}


def queue_webhooks(db, tenant_id, event_id, event_type):
    for hook in db.scalars(
        select(Webhook).where(Webhook.tenant_id == tenant_id, Webhook.enabled == True)
    ).all():
        if event_type in hook.events or "*" in hook.events:
            db.add(WebhookDelivery(tenant_id=tenant_id, webhook_id=hook.id, event_id=event_id))


@router.get("/inbox")
def inbox(tenant_id: str = Depends(auth()), db: Session = Depends(session)):
    rows = db.scalars(
        select(InboundMessage)
        .where(InboundMessage.tenant_id == tenant_id)
        .order_by(InboundMessage.received_at.desc())
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "from": r.sender,
                "to": r.destination,
                "content": r.content,
                "unread": r.unread,
                "conversation": r.conversation_key,
                "received_at": r.received_at,
            }
            for r in rows
        ]
    }


@router.post("/simulator/mo", status_code=202)
def simulate_mo(body: MOIn, x_simulator_token: str = Header(...), db: Session = Depends(session)):
    import os

    if not secrets.compare_digest(
        x_simulator_token, os.environ.get("BILLING_ADMIN_TOKEN", "disabled")
    ):
        raise HTTPException(403, "forbidden")
    prior = db.scalar(
        select(InboundMessage).where(
            InboundMessage.provider == body.provider,
            InboundMessage.provider_message_id == body.provider_message_id,
        )
    )
    if prior:
        return {"id": prior.id, "duplicate": True}
    contact = db.scalar(
        select(Contact).where(Contact.tenant_id == body.tenant_id, Contact.phone == body.sender)
    )
    keyword = body.content.strip().upper()
    event_type = "sms.received"
    policy = db.scalar(select(CountryPolicy).where(CountryPolicy.enabled == True))
    stop = set(policy.stop_keywords if policy else []) | {
        "STOP",
        "STOPALL",
        "UNSUBSCRIBE",
        "CANCEL",
        "END",
        "QUIT",
    }
    help_words = set(policy.help_keywords if policy else []) | {"HELP"}
    if keyword in stop:
        if not contact:
            contact = Contact(tenant_id=body.tenant_id, phone=body.sender)
        contact.consent_status = "opted_out"
        contact.opted_out_at = datetime.now(timezone.utc)
        contact.suppression_reason = "keyword:" + keyword
        db.add(contact)
        event_type = "sms.opted_out"
    elif keyword in help_words:
        event_type = "sms.help_requested"
    elif keyword == "START":
        if not contact:
            contact = Contact(tenant_id=body.tenant_id, phone=body.sender)
        contact.consent_status = "opted_in"
        contact.consent_source = "keyword:START"
        contact.consent_at = datetime.now(timezone.utc)
        contact.opted_out_at = None
        contact.suppression_reason = None
        db.add(contact)
        event_type = "sms.opted_in"
    row = InboundMessage(
        tenant_id=body.tenant_id,
        provider=body.provider,
        provider_message_id=body.provider_message_id,
        sender=body.sender,
        destination=body.destination,
        content=body.content,
        contact_id=contact.id if contact else None,
        conversation_key=f"{body.tenant_id}:{body.sender}:{body.destination}",
    )
    db.add(row)
    db.flush()
    db.add(
        Audit(
            tenant_id=body.tenant_id,
            actor="simulator",
            action=event_type,
            target=row.id,
            correlation_id=str(uuid.uuid4()),
        )
    )
    queue_webhooks(db, body.tenant_id, row.id, event_type)
    db.commit()
    return {"id": row.id, "event": event_type, "duplicate": False}


@router.post("/simulator/dlr", status_code=202)
def simulate_dlr(body: DLRIn, x_simulator_token: str = Header(...), db: Session = Depends(session)):
    import os

    if not secrets.compare_digest(
        x_simulator_token, os.environ.get("BILLING_ADMIN_TOKEN", "disabled")
    ):
        raise HTTPException(403, "forbidden")
    msg = db.scalar(
        select(Message).where(Message.id == body.message_id, Message.tenant_id == body.tenant_id)
    )
    if not msg:
        raise HTTPException(404, "message_not_found")
    prior = db.scalar(
        select(MessageEvent).where(
            MessageEvent.tenant_id == body.tenant_id,
            MessageEvent.external_event_id == body.external_event_id,
        )
    )
    if prior:
        return {"event_id": prior.id, "duplicate": True}
    row = MessageEvent(
        tenant_id=body.tenant_id,
        message_id=msg.id,
        external_event_id=body.external_event_id,
        type="dlr",
        status=body.status,
        provider_response=body.provider_response,
    )
    msg.status = body.status
    db.add(row)
    db.flush()
    queue_webhooks(db, body.tenant_id, row.id, "sms." + body.status)
    db.commit()
    return {"event_id": row.id, "status": msg.status, "duplicate": False}


@router.get("/admin/routes/preview")
def route_preview(
    destination: str,
    tenant_id: str | None = None,
    x_admin_token: str = Header(...),
    db: Session = Depends(session),
):
    import os

    if not secrets.compare_digest(x_admin_token, os.environ.get("BILLING_ADMIN_TOKEN", "disabled")):
        raise HTTPException(403, "forbidden")
    routes = db.scalars(select(Route).where(Route.enabled == True)).all()
    candidates = []
    for r in routes:
        p = db.get(Provider, r.provider_id)
        if (
            destination.startswith(r.prefix)
            and r.tenant_id in (None, tenant_id)
            and p
            and p.state == "enabled"
            and p.circuit_state == "closed"
        ):
            candidates.append((r, p))
    candidates.sort(
        key=lambda x: (
            x[0].tenant_id == tenant_id,
            len(x[0].prefix),
            x[1].health_score,
            x[0].priority,
        ),
        reverse=True,
    )
    return {
        "selected": (
            {
                "route_id": candidates[0][0].id,
                "provider": candidates[0][1].name,
                "simulator": candidates[0][1].connector.startswith("simulator"),
            }
            if candidates
            else None
        ),
        "dry_run": True,
    }


@router.get("/admin/finance/summary")
def finance_summary(x_admin_token: str = Header(...), db: Session = Depends(session)):
    import os

    if not secrets.compare_digest(x_admin_token, os.environ.get("BILLING_ADMIN_TOKEN", "disabled")):
        raise HTTPException(403, "forbidden")
    revenue, cost, messages, segments = db.execute(
        select(
            func.coalesce(func.sum(Usage.revenue), 0),
            func.coalesce(func.sum(Usage.cost), 0),
            func.count(Usage.id),
            func.coalesce(func.sum(Usage.segments), 0),
        )
    ).one()
    margin = revenue - cost
    return {
        "revenue": str(revenue),
        "provider_cost": str(cost),
        "gross_margin": str(margin),
        "margin_percent": str(
            (margin / revenue * 100).quantize(Decimal(".01")) if revenue else Decimal(0)
        ),
        "messages": messages,
        "segments": segments,
    }
