import hashlib
import math
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select
from .models import (
    Wallet,
    BillingAccount,
    LedgerEntry,
    Reservation,
    Rate,
    Message,
    Usage,
    Outbox,
)

GSM_BASIC = set(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXT = set("^{}\\[~]|€")


def segment_info(text):
    gsm = all(c in GSM_BASIC or c in GSM_EXT for c in text)
    if gsm:
        units = sum(2 if c in GSM_EXT else 1 for c in text)
        return "GSM-7", len(text), 1 if units <= 160 else math.ceil(units / 153)
    units = len(text.encode("utf-16-be")) // 2
    return "UCS-2", len(text), 1 if units <= 70 else math.ceil(units / 67)


def money(v):
    return Decimal(str(v)).quantize(Decimal("0.000001"))


def event(db, tenant, event_type, key, correlation, payload):
    eid = str(uuid.uuid4())
    db.add(
        Outbox(
            id=eid,
            tenant_id=tenant,
            event_type=event_type,
            idempotency_key=key,
            correlation_id=correlation,
            envelope={
                "event_id": eid,
                "event_type": event_type,
                "event_version": "1.0",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tenant_id": tenant,
                "correlation_id": correlation,
                "idempotency_key": key,
                "source_service": "telnexa-billing",
                "payload": payload,
                "metadata": {},
            },
        )
    )


def resolve_rate(db, kind, tenant, country, destination, plan_id=None, when=None):
    when = when or datetime.now(timezone.utc)
    rates = db.scalars(
        select(Rate)
        .where(Rate.kind == kind, Rate.country == country, Rate.effective_from <= when)
        .where((Rate.effective_to == None) | (Rate.effective_to > when))
    ).all()
    candidates = [
        r
        for r in rates
        if destination.startswith(r.prefix)
        and (r.tenant_id in (None, tenant))
        and (r.plan_id in (None, plan_id))
    ]
    if not candidates:
        raise ValueError(f"no_{kind}_rate")
    candidates.sort(
        key=lambda r: (
            r.tenant_id == tenant,
            r.plan_id == plan_id,
            len(r.prefix),
            r.priority,
        ),
        reverse=True,
    )
    return candidates[0]


def credit(db, account_id, amount, key, actor="system", reference="manual", correlation=None):
    acct = db.get(BillingAccount, account_id)
    wallet = db.scalar(
        select(Wallet).where(Wallet.billing_account_id == account_id).with_for_update()
    )
    amount = money(amount)
    correlation = correlation or str(uuid.uuid4())
    prior = db.scalar(
        select(LedgerEntry).where(
            LedgerEntry.tenant_id == acct.tenant_id, LedgerEntry.idempotency_key == key
        )
    )
    if prior:
        return prior
    wallet.available += amount
    wallet.version += 1
    entry = LedgerEntry(
        tenant_id=acct.tenant_id,
        billing_account_id=account_id,
        currency=wallet.currency,
        amount=amount,
        direction="credit",
        type="credit",
        reference_type="adjustment",
        reference_id=reference,
        idempotency_key=key,
        actor=actor,
    )
    db.add(entry)
    event(
        db,
        acct.tenant_id,
        "billing.wallet.credited",
        f"event:{key}",
        correlation,
        {
            "amount": str(amount),
            "currency": wallet.currency,
            "ledger_entry_id": entry.id,
        },
    )
    db.flush()
    return entry


def reserve(db, account_id, amount, key, message_id, correlation):
    acct = db.get(BillingAccount, account_id)
    wallet = db.scalar(
        select(Wallet).where(Wallet.billing_account_id == account_id).with_for_update()
    )
    prior = db.scalar(
        select(Reservation).where(
            Reservation.tenant_id == acct.tenant_id, Reservation.idempotency_key == key
        )
    )
    if prior:
        return prior
    amount = money(amount)
    available_credit = wallet.available + (
        acct.credit_limit if acct.billing_type == "postpaid" else Decimal(0)
    )
    if acct.frozen or available_credit < amount:
        raise ValueError("insufficient_balance")
    wallet.available -= amount
    wallet.reserved += amount
    wallet.version += 1
    r = Reservation(
        tenant_id=acct.tenant_id,
        billing_account_id=account_id,
        amount=amount,
        currency=wallet.currency,
        idempotency_key=key,
        message_id=message_id,
    )
    db.add(r)
    event(
        db,
        acct.tenant_id,
        "billing.reservation.created",
        f"event:{key}",
        correlation,
        {"reservation_id": r.id, "amount": str(amount)},
    )
    db.flush()
    return r


def release(db, reservation_id, correlation, reason="submission_failed"):
    r = db.scalar(select(Reservation).where(Reservation.id == reservation_id).with_for_update())
    if r.status != "active":
        return r
    wallet = db.scalar(
        select(Wallet).where(Wallet.billing_account_id == r.billing_account_id).with_for_update()
    )
    wallet.reserved -= r.amount
    wallet.available += r.amount
    r.status = "released"
    r.finalized_at = datetime.now(timezone.utc)
    event(
        db,
        r.tenant_id,
        "billing.reservation.released",
        f"release:{r.id}",
        correlation,
        {"reservation_id": r.id, "reason": reason},
    )
    return r


def finalize(db, reservation_id, correlation):
    r = db.scalar(select(Reservation).where(Reservation.id == reservation_id).with_for_update())
    if r.status != "active":
        return r
    wallet = db.scalar(
        select(Wallet).where(Wallet.billing_account_id == r.billing_account_id).with_for_update()
    )
    wallet.reserved -= r.amount
    r.status = "finalized"
    r.finalized_at = datetime.now(timezone.utc)
    entry = LedgerEntry(
        tenant_id=r.tenant_id,
        billing_account_id=r.billing_account_id,
        currency=r.currency,
        amount=r.amount,
        direction="debit",
        type="message_charge",
        reference_type="message",
        reference_id=r.message_id,
        idempotency_key=f"charge:{r.id}",
        actor="billing-engine",
    )
    db.add(entry)
    event(
        db,
        r.tenant_id,
        "billing.charge.finalized",
        f"event:charge:{r.id}",
        correlation,
        {"reservation_id": r.id, "amount": str(r.amount)},
    )
    return r


def send_simulated(
    db,
    account_id,
    destination,
    sender,
    content,
    key,
    correlation,
    outcome="delivered",
    provider="simulator",
    request_hash=None,
):
    acct = db.get(BillingAccount, account_id)
    prior = db.scalar(
        select(Message).where(Message.tenant_id == acct.tenant_id, Message.idempotency_key == key)
    )
    if prior:
        return prior
    encoding, chars, segments = segment_info(content)
    country = "ZZ"
    cost = resolve_rate(db, "provider", acct.tenant_id, country, destination)
    sell = resolve_rate(db, "sell", acct.tenant_id, country, destination)
    msg = Message(
        tenant_id=acct.tenant_id,
        idempotency_key=key,
        request_hash=request_hash
        or hashlib.sha256((destination + "\n" + sender + "\n" + content).encode()).hexdigest(),
        correlation_id=correlation,
        destination=destination,
        sender=sender,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        encoding=encoding,
        character_count=chars,
        segments=segments,
        provider=provider,
        status="reserved",
        provider_rate_snapshot={"id": cost.id, "amount": str(cost.amount)},
        sell_rate_snapshot={"id": sell.id, "amount": str(sell.amount)},
        estimated_provider_cost=money(cost.amount * segments),
        estimated_sell_amount=money(sell.amount * segments),
    )
    db.add(msg)
    db.flush()
    r = reserve(db, account_id, msg.estimated_sell_amount, f"send:{key}", msg.id, correlation)
    msg.reservation_id = r.id
    if outcome in {"reject", "submission_failed"}:
        release(db, r.id, correlation, outcome)
        msg.status = "failed"
        return msg
    finalize(db, r.id, correlation)
    msg.status = outcome
    msg.provider_message_id = f"sim-{uuid.uuid4()}"
    msg.actual_provider_cost = msg.estimated_provider_cost
    msg.actual_sell_amount = msg.estimated_sell_amount
    db.add(
        Usage(
            tenant_id=msg.tenant_id,
            message_id=msg.id,
            country=country,
            provider=provider,
            sender=sender,
            segments=segments,
            status=outcome,
            revenue=msg.actual_sell_amount,
            cost=msg.actual_provider_cost,
        )
    )
    return msg
