from datetime import datetime, timezone
from sqlalchemy import select
from .models import Message, Reservation, SmsDispatchJob, SmsReconciliationCase, Usage


def scan(db):
    created = 0
    for message in db.scalars(select(Message)).all():
        checks = []
        reservation = (
            db.get(Reservation, message.reservation_id) if message.reservation_id else None
        )
        job = db.get(SmsDispatchJob, message.dispatch_job_id) if message.dispatch_job_id else None
        usage = db.scalar(
            select(Usage).where(
                Usage.tenant_id == message.tenant_id, Usage.message_id == message.id
            )
        )
        if message.status == "submission_unknown":
            checks.append("ambiguous_submission")
        if message.status in {"submitted", "sent", "delivered"} and not usage:
            checks.append("usage_missing")
        if (
            message.status in {"submitted", "sent", "delivered"}
            and reservation
            and reservation.status != "finalized"
        ):
            checks.append("billing_status_drift")
        if (
            message.status
            in {"delivered", "rejected", "failed", "expired", "undeliverable", "cancelled"}
            and job
            and job.state in {"queued", "dispatching", "retry_wait"}
        ):
            checks.append("terminal_message_active_job")
        for kind in checks:
            if not db.scalar(
                select(SmsReconciliationCase).where(
                    SmsReconciliationCase.case_type == kind,
                    SmsReconciliationCase.reference_id == message.id,
                )
            ):
                db.add(
                    SmsReconciliationCase(
                        tenant_id=message.tenant_id,
                        message_id=message.id,
                        case_type=kind,
                        reference_id=message.id,
                        evidence={"message_status": message.status},
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                created += 1
    return created
