"""DLR, inbound-MO, compliance, usage, and provider-health behavior."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from .sms_provider_contracts import (
    MIDDLEWARE_SMS_SHA,
    SDK_CONTRACT_SHA,
    STATE_RANK,
    CallbackReplayConflict,
    DlrCallback,
    MoCallback,
    ProviderNotFound,
    compliance_action,
    segment_info,
    sha256,
)
from .sms_provider_models import (
    SmsCallbackOutbox,
    SmsInbound,
    SmsOperation,
    SmsOptOut,
    SmsProviderEvent,
)
from .sms_provider_transport import _canonical_provider_status


class SmsProviderEventsMixin:
    def record_dlr(
        self,
        callback: DlrCallback,
        *,
        event_id: str,
        payload_digest: str,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            prior = session.scalar(
                select(SmsProviderEvent).where(
                    SmsProviderEvent.tenant_id == callback.tenant_id,
                    SmsProviderEvent.external_event_id == event_id,
                )
            )
            if prior:
                if prior.payload_sha256 != payload_digest:
                    raise CallbackReplayConflict(
                        "provider event identity was reused with different content"
                    )
                return {"accepted": True, "duplicate": True, "status": prior.canonical_status}

            query = select(SmsOperation).where(SmsOperation.tenant_id == callback.tenant_id)
            if callback.message_id:
                query = query.where(
                    SmsOperation.middleware_message_id == str(callback.message_id)
                )
            else:
                query = query.where(
                    SmsOperation.provider_reference == callback.provider_reference
                )
            operation = session.scalar(query)
            if not operation:
                raise ProviderNotFound("DLR does not match a tenant SMS operation")

            target = _canonical_provider_status(callback.provider_status)
            current_rank = STATE_RANK.get(operation.state, 0)
            target_rank = STATE_RANK.get(target, 0)
            ignored = (
                operation.state == "delivered" and target != "delivered"
            ) or (target_rank < current_rank)
            if not ignored:
                operation.state = target
                operation.provider_status = callback.provider_status
                operation.provider_reference = (
                    callback.provider_reference or operation.provider_reference
                )
                operation.last_error = callback.failure_message

            self._event(
                session,
                operation,
                event_id=event_id,
                event_type=(
                    "sms.message.status.v1"
                    if ignored
                    else "sms.message.delivered.v1"
                    if target == "delivered"
                    else "sms.message.failed.v1"
                    if target in {"failed", "expired"}
                    else "sms.message.status.v1"
                ),
                canonical_status=operation.state,
                provider_status=callback.provider_status,
                payload={
                    "failure_code": callback.failure_code,
                    "failure_message": callback.failure_message,
                },
                occurred_at=callback.occurred_at,
                ignored_transition=ignored,
            ).payload_sha256 = payload_digest
            session.commit()
            return {
                "accepted": True,
                "duplicate": False,
                "status": operation.state,
                "ignored": ignored,
            }

    def record_mo(
        self,
        callback: MoCallback,
        *,
        event_id: str,
        payload_digest: str,
    ) -> dict[str, Any]:
        with self.session_factory() as session:
            prior_event = session.scalar(
                select(SmsProviderEvent).where(
                    SmsProviderEvent.tenant_id == callback.tenant_id,
                    SmsProviderEvent.external_event_id == event_id,
                )
            )
            if prior_event:
                if prior_event.payload_sha256 != payload_digest:
                    raise CallbackReplayConflict(
                        "provider event identity was reused with different content"
                    )
                return {
                    "accepted": True,
                    "duplicate": True,
                    "action": prior_event.payload.get("compliance_action"),
                }

            prior_message = session.scalar(
                select(SmsInbound).where(
                    SmsInbound.tenant_id == callback.tenant_id,
                    SmsInbound.provider == "jasmin",
                    SmsInbound.provider_message_id == callback.provider_message_id,
                )
            )
            if prior_message:
                return {
                    "accepted": True,
                    "duplicate": True,
                    "action": prior_message.compliance_action,
                }

            info = segment_info(callback.content)
            action = compliance_action(callback.content)
            inbound = SmsInbound(
                tenant_id=callback.tenant_id,
                provider="jasmin",
                provider_message_id=callback.provider_message_id,
                sender=callback.sender,
                destination=callback.destination,
                content_sha256=sha256(callback.content),
                encoding=info.encoding,
                characters=info.characters,
                segments=info.segments,
                compliance_action=action,
                occurred_at=callback.occurred_at,
            )
            session.add(inbound)
            if action == "stop":
                existing = session.scalar(
                    select(SmsOptOut).where(
                        SmsOptOut.tenant_id == callback.tenant_id,
                        SmsOptOut.phone == callback.sender,
                        SmsOptOut.scope_key == "tenant",
                    )
                )
                if existing:
                    existing.active = True
                    existing.reason = "keyword:STOP"
                else:
                    session.add(
                        SmsOptOut(
                            tenant_id=callback.tenant_id,
                            phone=callback.sender,
                            scope_key="tenant",
                            reason="keyword:STOP",
                        )
                    )
            event_type = (
                "sms.recipient.opted-out.v1"
                if action == "stop"
                else "sms.help-requested.v1"
                if action == "help"
                else "sms.inbound.received.v1"
            )
            payload = {
                "tenant_id": callback.tenant_id,
                "inbound_message_id": inbound.id,
                "provider_reference": callback.provider_message_id,
                "from": callback.sender,
                "to": callback.destination,
                "content_sha256": inbound.content_sha256,
                "encoding": info.encoding,
                "characters": info.characters,
                "segments": info.segments,
                "compliance_action": action,
            }
            event = self._event(
                session,
                None,
                event_id=event_id,
                event_type=event_type,
                canonical_status="delivered",
                provider_status="received",
                payload=payload,
                occurred_at=callback.occurred_at,
            )
            event.payload_sha256 = payload_digest
            session.commit()
            return {"accepted": True, "duplicate": False, "action": action}

    def opt_outs(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(SmsOptOut).where(
                    SmsOptOut.tenant_id == tenant_id,
                    SmsOptOut.active.is_(True),
                )
            ).all()
            return [
                {
                    "phone": row.phone,
                    "scope": row.scope_key,
                    "reason": row.reason,
                    "created_at": row.created_at.isoformat(),
                }
                for row in rows
            ]

    def usage(self, tenant_id: str) -> dict[str, Any]:
        with self.session_factory() as session:
            messages, segments, submission_attempts, reconciliation_attempts = session.execute(
                select(
                    func.count(SmsOperation.id),
                    func.coalesce(func.sum(SmsOperation.segments), 0),
                    func.coalesce(func.sum(SmsOperation.submission_attempts), 0),
                    func.coalesce(func.sum(SmsOperation.reconciliation_attempts), 0),
                ).where(SmsOperation.tenant_id == tenant_id)
            ).one()
            return {
                "messages": messages,
                "segments": segments,
                "provider_submission_attempts": submission_attempts,
                "provider_resubmissions": 0,
                "reconciliation_readbacks": reconciliation_attempts,
                "live_submission": False,
            }

    def callbacks(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(SmsCallbackOutbox)
                .where(SmsCallbackOutbox.tenant_id == tenant_id)
                .order_by(SmsCallbackOutbox.created_at)
            ).all()
            return [
                {
                    "event_id": row.event_id,
                    "event_type": row.event_type,
                    "state": row.state,
                    "payload": row.payload,
                }
                for row in rows
            ]

    def health(self) -> dict[str, Any]:
        with self.session_factory() as session:
            session.execute(select(1))
        transport_health = self.transport.health()
        return {
            "status": "ok",
            "database": "ok",
            "transport": transport_health,
            "sdk_contract_sha": SDK_CONTRACT_SHA,
            "middleware_sms_sha": MIDDLEWARE_SMS_SHA,
            "sms_delivery": False,
            "live_sms_delivery": False,
            "jasmin_live_submission": False,
        }
