import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from .adapters.base import NormalizedSubmission, SubmissionOutcome
from .engine import event, finalize, money, release, reserve, resolve_rate, segment_info
from .models import (
    Audit,
    BillingAccount,
    Message,
    SmsDispatchAttempt,
    SmsDispatchJob,
    SmsReconciliationCase,
    SmsProductionCanaryGate,
    Usage,
)
from .routing import choose_route
from .state_machine import transition


def accept_message(
    db,
    account_id,
    destination,
    sender,
    content,
    category,
    key,
    correlation,
    request_hash,
    actor="commercial-api",
):
    account = db.get(BillingAccount, account_id)
    prior = db.scalar(
        select(Message).where(
            Message.tenant_id == account.tenant_id, Message.idempotency_key == key
        )
    )
    if prior:
        return prior
    encoding, chars, segments = segment_info(content)
    cost = resolve_rate(db, "provider", account.tenant_id, "ZZ", destination)
    sell = resolve_rate(db, "sell", account.tenant_id, "ZZ", destination)
    message = Message(
        tenant_id=account.tenant_id,
        idempotency_key=key,
        request_hash=request_hash,
        correlation_id=correlation,
        destination=destination,
        sender=sender,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        encoding=encoding,
        character_count=chars,
        segments=segments,
        provider="pending",
        status="accepted",
        provider_rate_snapshot={"id": cost.id, "amount": str(cost.amount)},
        sell_rate_snapshot={"id": sell.id, "amount": str(sell.amount)},
        estimated_provider_cost=money(cost.amount * segments),
        estimated_sell_amount=money(sell.amount * segments),
    )
    db.add(message)
    db.flush()
    reservation = reserve(
        db, account.id, message.estimated_sell_amount, f"send:{key}", message.id, correlation
    )
    message.reservation_id = reservation.id
    transition(
        db,
        message,
        "queued",
        f"accept:{message.id}",
        "sms.accepted",
        {"category": category, "segments": segments},
    )
    job = SmsDispatchJob(tenant_id=message.tenant_id, message_id=message.id, state="queued")
    db.add(job)
    db.flush()
    message.dispatch_job_id = job.id
    db.add(
        Audit(
            tenant_id=message.tenant_id,
            actor=actor,
            action="sms.accepted",
            target=message.id,
            correlation_id=correlation,
            before={},
            after={"status": "queued", "dispatch_job_id": job.id},
        )
    )
    return message


def claim_job(db, owner, lease_seconds=60):
    now = datetime.now(timezone.utc)
    query = (
        select(SmsDispatchJob)
        .where(
            SmsDispatchJob.state.in_(["queued", "retry_wait"]),
            SmsDispatchJob.available_at <= now,
            (SmsDispatchJob.lease_expires_at == None) | (SmsDispatchJob.lease_expires_at < now),
        )
        .order_by(SmsDispatchJob.priority.desc(), SmsDispatchJob.available_at)
        .limit(1)
    )
    if db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    job = db.scalar(query)
    if job:
        job.lease_owner = owner
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.state = "dispatching"
        job.updated_at = now
        transition(
            db,
            db.get(Message, job.message_id),
            "dispatching",
            f"dispatch:{job.id}:{job.attempt_count + 1}",
        )
    return job


def process_job(db, job, adapter_factory):
    message = db.get(Message, job.message_id)
    selection = choose_route(db, message)
    if not selection:
        release(db, message.reservation_id, message.correlation_id, "no_route")
        transition(db, message, "rejected", f"no-route:{job.id}", evidence={"reason": "no_route"})
        job.state = "rejected"
        job.last_error_code = "no_route"
        return
    provider, route, decision = selection.provider, selection.route, selection.decision
    job.selected_provider_id = provider.id
    job.route_decision_id = decision.id
    message.route_decision_id = decision.id
    message.provider = provider.name
    now = datetime.now(timezone.utc)
    inflight = db.scalar(
        select(func.count())
        .select_from(SmsDispatchJob)
        .where(
            SmsDispatchJob.selected_provider_id == provider.id,
            SmsDispatchJob.state == "dispatching",
        )
    )
    recent = db.scalar(
        select(func.count())
        .select_from(SmsDispatchAttempt)
        .where(
            SmsDispatchAttempt.provider_id == provider.id,
            SmsDispatchAttempt.started_at >= now - timedelta(seconds=1),
        )
    )
    if inflight > provider.max_inflight or recent >= provider.tps:
        transition(db, message, "retry_wait", f"throttle:{job.id}:{job.attempt_count + 1}")
        job.state = "retry_wait"
        job.available_at = now + timedelta(seconds=1)
        job.lease_owner = None
        job.lease_expires_at = None
        return
    job.attempt_count += 1
    attempt = SmsDispatchAttempt(
        job_id=job.id,
        message_id=message.id,
        tenant_id=message.tenant_id,
        attempt_number=job.attempt_count,
        provider_id=provider.id,
        adapter_type=provider.adapter_type,
        route_version=route.version,
        request_fingerprint=message.content_hash,
    )
    db.add(attempt)
    db.flush()
    submission = NormalizedSubmission(
        message.id,
        message.tenant_id,
        message.correlation_id,
        message.destination,
        message.sender,
        message.content or "",
        message.encoding,
        message.segments,
        message.id,
    )
    result = adapter_factory(provider).submit(submission)
    attempt.completed_at = datetime.now(timezone.utc)
    attempt.outcome = result.outcome.value
    attempt.provider_message_id = result.provider_message_id
    attempt.http_status = result.http_status
    attempt.provider_code = result.provider_code
    if result.outcome == SubmissionOutcome.ACCEPTED:
        message.provider_message_id = result.provider_message_id
        message.submission_certainty = "certain"
        transition(
            db,
            message,
            "submitted",
            f"submit:{attempt.id}",
            evidence={"provider_code": result.provider_code},
        )
        finalize(db, message.reservation_id, message.correlation_id)
        if not db.scalar(
            select(Usage).where(
                Usage.tenant_id == message.tenant_id, Usage.message_id == message.id
            )
        ):
            db.add(
                Usage(
                    tenant_id=message.tenant_id,
                    message_id=message.id,
                    country="ZZ",
                    provider=message.provider,
                    sender=message.sender,
                    segments=message.segments,
                    status="submitted",
                    revenue=message.estimated_sell_amount,
                    cost=message.estimated_provider_cost,
                )
            )
        event(
            db,
            message.tenant_id,
            "sms.submitted",
            f"sms:submitted:{message.id}",
            message.correlation_id,
            {"message_id": message.id},
        )
        gate = db.scalar(
            select(SmsProductionCanaryGate).where(
                SmsProductionCanaryGate.enabled == True,
                SmsProductionCanaryGate.allowed_tenant == message.tenant_id,
                SmsProductionCanaryGate.allowed_sender == message.sender,
            )
        )
        if gate and message.destination in gate.allowed_destinations:
            gate.claimed_count += 1
            gate.updated_at = datetime.now(timezone.utc)
            if gate.claimed_count >= gate.max_submissions:
                gate.enabled = False
        job.state = "submitted"
    elif result.outcome == SubmissionOutcome.AMBIGUOUS:
        message.submission_certainty = "unknown"
        transition(
            db,
            message,
            "submission_unknown",
            f"ambiguous:{attempt.id}",
            evidence={"provider_code": result.provider_code},
        )
        job.state = "reconciliation"
        db.add(
            SmsReconciliationCase(
                tenant_id=message.tenant_id,
                message_id=message.id,
                case_type="ambiguous_submission",
                reference_id=attempt.id,
                evidence={"provider_id": provider.id, "provider_code": result.provider_code},
            )
        )
    elif result.outcome == SubmissionOutcome.SAFE_RETRY and job.attempt_count < job.max_attempts:
        transition(db, message, "retry_wait", f"retry:{attempt.id}")
        job.state = "retry_wait"
        job.available_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    else:
        release(
            db, message.reservation_id, message.correlation_id, result.provider_code or "rejected"
        )
        transition(
            db,
            message,
            "rejected",
            f"reject:{attempt.id}",
            evidence={"provider_code": result.provider_code},
        )
        job.state = "rejected"
    job.lease_owner = None
    job.lease_expires_at = None
    job.updated_at = datetime.now(timezone.utc)
