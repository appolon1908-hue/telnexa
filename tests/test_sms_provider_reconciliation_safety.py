from __future__ import annotations

import uuid
from decimal import Decimal

from billing.sms_provider_runtime import (
    ReadbackResult,
    ScriptedJasminTransport,
    SmsCommand,
    SmsOperation,
    SmsProviderService,
    SmsReconciliationEvidence,
    SubmissionResult,
    create_session_factory,
    segment_info,
)

TENANT = "tenant-reconciliation-safety"


def _command(key: str) -> SmsCommand:
    content = "reconciliation safety"
    segments = segment_info(content)
    message_id = uuid.uuid5(uuid.NAMESPACE_URL, f"telnexa:{key}:message")
    return SmsCommand.model_validate(
        {
            "command_id": str(
                uuid.uuid5(uuid.NAMESPACE_URL, f"telnexa:{key}:command")
            ),
            "command_type": "sms.message.submit.v1",
            "command_version": "1.0",
            "target": "telnexa-sms",
            "tenant_id": TENANT,
            "requested_by": "middleware-reconciliation-test",
            "correlation_id": f"correlation-{key}",
            "idempotency_key": key,
            "capability": "SMS_DELIVERY",
            "payload": {
                "message_id": str(message_id),
                "channel": "sms",
                "destination": "+18095550123",
                "sender": "CODESTRA",
                "content": content,
                "encoding": segments.encoding,
                "characters": segments.characters,
                "segments": segments.segments,
                "category": "transactional",
                "client_reference": str(message_id),
                "scheduled_at": None,
                "billing_account_id": "billing-reconciliation-test",
                "campaign_id": None,
            },
        }
    )


def _service(tmp_path, transport: ScriptedJasminTransport) -> SmsProviderService:
    _, session_factory = create_session_factory(
        f"sqlite:///{tmp_path / 'reconciliation.db'}"
    )
    return SmsProviderService(
        session_factory=session_factory,
        transport=transport,
        sell_price_per_segment=Decimal("0.05"),
    )


def test_stale_submitting_operation_recovers_by_readback_without_resubmit(
    tmp_path,
) -> None:
    transport = ScriptedJasminTransport(
        submissions=[SubmissionResult("unknown", error="timeout_after_acceptance")],
        readbacks=[
            ReadbackResult(
                "accepted",
                provider_reference="jasmin-recovered-reference",
                provider_status="accepted",
            )
        ],
    )
    provider = _service(tmp_path, transport)
    submitted = provider.submit(_command("stale-submitting-key"))

    with provider.session_factory() as session:
        operation = session.get(SmsOperation, submitted["operation_id"])
        assert operation is not None
        operation.state = "submitting"
        operation.last_error = None
        session.commit()

    recovered = provider.reconcile(TENANT, submitted["operation_id"])

    assert recovered["state"] == "provider_accepted"
    assert recovered["provider_reference"] == "jasmin-recovered-reference"
    assert recovered["submission_attempts"] == 1
    assert recovered["reconciliation_attempts"] == 1
    assert recovered["billing"]["state"] == "committed"
    assert transport.submit_calls == 1
    assert transport.readback_calls == 1


def test_reference_less_accepted_readback_remains_unresolved_and_unbilled(
    tmp_path,
) -> None:
    transport = ScriptedJasminTransport(
        submissions=[SubmissionResult("unknown", error="timeout_after_acceptance")],
        readbacks=[ReadbackResult("accepted", provider_status="accepted")],
    )
    provider = _service(tmp_path, transport)
    submitted = provider.submit(_command("reference-required-key"))

    reconciled = provider.reconcile(TENANT, submitted["operation_id"])

    assert reconciled["state"] == "reconciliation_required"
    assert reconciled["provider_reference"] is None
    assert reconciled["billing"]["state"] == "reserved"
    assert "durable provider reference" in reconciled["last_error"]
    assert transport.submit_calls == 1
    assert transport.readback_calls == 1


def test_serial_attempt_numbers_and_evidence_remain_unique(tmp_path) -> None:
    transport = ScriptedJasminTransport(
        submissions=[SubmissionResult("unknown", error="timeout_after_acceptance")],
        readbacks=[
            ReadbackResult("unknown", error="readback_temporarily_unavailable"),
            ReadbackResult(
                "accepted",
                provider_reference="jasmin-serialized-reference",
                provider_status="accepted",
            ),
        ],
    )
    provider = _service(tmp_path, transport)
    submitted = provider.submit(_command("serialized-attempt-key"))

    first = provider.reconcile(TENANT, submitted["operation_id"])
    second = provider.reconcile(TENANT, submitted["operation_id"])

    assert first["reconciliation_attempts"] == 1
    assert second["reconciliation_attempts"] == 2
    assert second["state"] == "provider_accepted"
    assert transport.submit_calls == 1
    assert transport.readback_calls == 2

    with provider.session_factory() as session:
        evidence = (
            session.query(SmsReconciliationEvidence)
            .filter(
                SmsReconciliationEvidence.operation_id
                == submitted["operation_id"]
            )
            .order_by(SmsReconciliationEvidence.attempt_number)
            .all()
        )
        assert [row.attempt_number for row in evidence] == [1, 2]
