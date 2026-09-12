import hashlib
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .adapters.base import NormalizedSubmission, SubmissionOutcome
from .engine import event, finalize, money, release, reserve, segment_info
from .provider_capacity import acquire_provider_capacity, release_provider_capacity
from .production_gates import production_enabled, validate_reserved_canary
from .production_policy import (
    ProductionPolicyDenied,
    reserve_delivery_authority,
    validate_reserved_policy,
)
from .models import (
    Audit,
    BillingAccount,
    Message,
    SmsDispatchAttempt,
    SmsDispatchJob,
    SmsReconciliationCase,
    SmsRouteDecision,
    Provider,
    Route,
    Tenant,
    Usage,
)
from .routing import authorize_route, persist_decision
from .state_machine import transition
from .sms_integration import SmsAcceptanceReceipt, enforce_message_policy


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
    canary_gate_id=None,
    production_policy_id=None,
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
    tenant = db.get(Tenant, account.tenant_id)
    authorized = authorize_route(
        db, account.tenant_id, destination, sender, category, tenant.plan_id, encoding
    )
    cost, sell = authorized.provider_rate, authorized.sell_rate
    if production_enabled():
        authority = reserve_delivery_authority(
            db,
            tenant_id=account.tenant_id,
            sender=sender.sender,
            destination=destination,
            category=category,
            segments=segments,
            provider_id=authorized.provider.id,
            country=authorized.route.country,
            provider_cost=cost.amount * segments,
            provider_currency=cost.currency,
        )
        canary_gate_id = authority.canary_gate_id
        production_policy_id = authority.policy_id
    message = Message(
        tenant_id=account.tenant_id,
        idempotency_key=key,
        request_hash=request_hash,
        correlation_id=correlation,
        destination=destination,
        sender=sender.sender,
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        encoding=encoding,
        character_count=chars,
        segments=segments,
        provider=authorized.provider.name,
        status="accepted",
        provider_rate_snapshot={},
        sell_rate_snapshot={},
        estimated_provider_cost=money(cost.amount * segments),
        estimated_sell_amount=money(sell.amount * segments),
    )
    db.add(message)
    db.flush()
    decision = persist_decision(db, message, sender, authorized)
    message.route_decision_id = decision.id
    message.provider_rate_snapshot = decision.provider_rate_snapshot
    message.sell_rate_snapshot = decision.sell_rate_snapshot
    reservation = reserve(
        db,
        account.id,
        message.estimated_sell_amount,
        "send:" + hashlib.sha256(key.encode()).hexdigest(),
        message.id,
        correlation,
    )
    message.reservation_id = reservation.id
    transition(
        db,
        message,
        "queued",
        f"accept:{message.id}",
        "sms.accepted",
        {"category": category, "segments": segments, "route_decision_id": decision.id},
    )
    job = SmsDispatchJob(
        tenant_id=message.tenant_id,
        message_id=message.id,
        state="queued",
        selected_provider_id=authorized.provider.id,
        route_decision_id=decision.id,
        canary_gate_id=canary_gate_id,
        production_policy_id=production_policy_id,
    )
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
    decision = db.get(SmsRouteDecision, job.route_decision_id)
    provider = db.get(Provider, decision.selected_provider_id) if decision else None
    route = db.get(Route, decision.selected_route_id) if decision else None
    if not decision or not provider or not route:
        release(db, message.reservation_id, message.correlation_id, "route_decision_missing")
        transition(
            db,
            message,
            "rejected",
            f"no-route:{job.id}",
            evidence={"reason": "route_decision_missing"},
        )
        job.state = "rejected"
        job.last_error_code = "route_decision_missing"
        return
    if (
        message.provider_rate_snapshot != decision.provider_rate_snapshot
        or message.sell_rate_snapshot != decision.sell_rate_snapshot
        or message.provider != provider.name
        or decision.country != route.country
        or decision.route_version != route.version
        or job.selected_provider_id != provider.id
    ):
        release(db, message.reservation_id, message.correlation_id, "route_rate_consensus_failed")
        transition(
            db,
            message,
            "rejected",
            f"route-rate:{job.id}",
            evidence={"reason": "route_rate_consensus_failed"},
        )
        job.state = "rejected"
        job.last_error_code = "route_rate_consensus_failed"
        return
    now = datetime.now(timezone.utc)
    gate = None
    # Same tenant -> policy/canary lock order as the acceptance path.
    db.scalar(select(Tenant).where(Tenant.id == message.tenant_id).with_for_update())
    receipt = db.get(SmsAcceptanceReceipt, message.id)
    if production_enabled():
        try:
            policy = validate_reserved_policy(
                db,
                policy_id=job.production_policy_id,
                tenant_id=message.tenant_id,
                sender=message.sender,
                destination=message.destination,
                category=receipt.category if receipt else "transactional",
                provider_id=provider.id,
                country=decision.country,
                now=now,
            )
        except ProductionPolicyDenied as exc:
            release(db, message.reservation_id, message.correlation_id, exc.code)
            transition(
                db,
                message,
                "rejected",
                f"production-policy-denied:{job.id}",
                evidence={"reason": exc.code},
            )
            job.state = "rejected"
            job.last_error_code = exc.code
            return
        if policy.mode == "TRANSACTIONAL_CANARY":
            gate = validate_reserved_canary(
                db,
                job.canary_gate_id,
                message.tenant_id,
                message.sender,
                message.destination,
                now,
            )
            if not gate:
                release(db, message.reservation_id, message.correlation_id, "canary_gate_denied")
                transition(
                    db,
                    message,
                    "rejected",
                    f"canary-denied:{job.id}",
                    evidence={"reason": "canary_gate_denied"},
                )
                job.state = "rejected"
                job.last_error_code = "canary_gate_denied"
                return
    from fastapi import HTTPException

    try:
        if production_enabled() and receipt is None:
            raise HTTPException(403, "submission_receipt_required")
        enforce_message_policy(
            db,
            message.tenant_id,
            message.destination,
            receipt.category if receipt else "transactional",
            receipt.campaign_id if receipt else None,
        )
    except HTTPException as exc:
        release(db, message.reservation_id, message.correlation_id, exc.detail)
        transition(
            db, message, "rejected", f"policy-denied:{job.id}", evidence={"reason": exc.detail}
        )
        job.state, job.last_error_code = "rejected", exc.detail
        return
    if not acquire_provider_capacity(db, provider.id, now):
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
    try:
        result = adapter_factory(provider).submit(submission)
    finally:
        release_provider_capacity(db, provider.id)
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
                    country=decision.country,
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
            {
                "message_id": message.id,
                "status": "provider_accepted",
                "provider_message_id": message.provider_message_id,
                "segments": message.segments,
                "message_idempotency_key": message.idempotency_key,
            },
        )
        if gate:
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
