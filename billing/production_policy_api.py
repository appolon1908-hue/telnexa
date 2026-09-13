"""Privileged, audited control surface for production SMS authorization."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import session
from .models import (
    Audit,
    BillingAccount,
    CommandIdempotency,
    Provider,
    Route,
    Sender,
    SmsDeliveryPolicy,
    SmsProductionAuthorization,
    SmsProductionAuthorizationRevocation,
    SmsProductionCanaryGate,
    SmsSystemControl,
    Tenant,
)
from .sms_integration import bounded_reference

router = APIRouter(prefix="/api/v1/admin/sms", tags=["Production SMS control"])
OPERATOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@-]{2,119}$")


class AuthorizationIn(BaseModel):
    change_id: str = Field(min_length=3, max_length=120)
    tenant_id: str = Field(min_length=1, max_length=36)
    production_owner: str = Field(min_length=3, max_length=120)
    approved_senders: list[str] = Field(min_length=1, max_length=20)
    approved_destinations: list[str] = Field(min_length=1, max_length=100)
    approved_categories: list[Literal["transactional", "service"]] = Field(
        min_length=1, max_length=2
    )
    per_minute_segments: int = Field(ge=1)
    per_hour_segments: int = Field(ge=1)
    per_day_segments: int = Field(ge=1)
    provider_id: str = Field(min_length=1, max_length=36)
    billing_account_id: str = Field(min_length=1, max_length=36)
    max_total_spend_minor: int = Field(ge=1, le=200)
    spending_currency: Literal["USD"]
    account_grain: Literal["billing_account"]
    valid_from: datetime
    valid_until: datetime
    monitoring_owner: str = Field(min_length=3, max_length=120)
    escalation_owner: str = Field(min_length=3, max_length=120)
    rollback_owner: str = Field(min_length=3, max_length=120)
    kill_switch_procedure: str = Field(min_length=10, max_length=500)
    approved_release_sha: str = Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
    approved_by: str = Field(min_length=3, max_length=120)
    review_at: datetime
    reason: str = Field(min_length=10, max_length=500)

    model_config = {"extra": "forbid"}

    @field_validator("approved_senders", "approved_destinations", "approved_categories")
    @classmethod
    def unique_values(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("duplicate policy value")
        return sorted(value)

    @field_validator("approved_senders")
    @classmethod
    def valid_senders(cls, value: list[str]) -> list[str]:
        if any(not 1 <= len(item) <= 20 or any(ord(char) < 33 for char in item) for item in value):
            raise ValueError("invalid sender")
        return value

    @field_validator("approved_destinations")
    @classmethod
    def valid_destinations(cls, value: list[str]) -> list[str]:
        if any(not re.fullmatch(r"\+[1-9][0-9]{5,18}", item) for item in value):
            raise ValueError("invalid E.164 destination")
        return value

    @model_validator(mode="after")
    def bounded_windows(self):
        for value in (self.valid_from, self.valid_until, self.review_at):
            if value.tzinfo is None:
                raise ValueError("authorization timestamps must include timezone")
        if (
            not self.valid_from < self.valid_until
            or not self.valid_from <= self.review_at <= self.valid_until
        ):
            raise ValueError("invalid authorization window")
        if not (self.per_minute_segments <= self.per_hour_segments <= self.per_day_segments):
            raise ValueError("segment limits must be monotonic")
        return self


class ActivationIn(BaseModel):
    authorization_id: str = Field(min_length=1, max_length=36)
    mode: Literal["TRANSACTIONAL_CANARY", "TRANSACTIONAL_PRODUCTION"]
    expected_policy_version: int = Field(ge=0)
    reason: str = Field(min_length=10, max_length=500)

    model_config = {"extra": "forbid"}


class PolicyMutationIn(BaseModel):
    expected_policy_version: int = Field(ge=1)
    reason: str = Field(min_length=10, max_length=500)

    model_config = {"extra": "forbid"}


class SenderKillSwitchIn(PolicyMutationIn):
    sender: str = Field(min_length=1, max_length=20)


class RevocationIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)

    model_config = {"extra": "forbid"}


class SystemKillSwitchIn(BaseModel):
    expected_control_version: int = Field(ge=0)
    reason: str = Field(min_length=10, max_length=500)

    model_config = {"extra": "forbid"}


@dataclass(frozen=True)
class Operator:
    actor: str
    correlation_id: str
    idempotency_key: str


def _authorized_actor(x_production_operator_token: str, x_operator_id: str) -> str:
    path = os.environ.get(
        "TELNEXA_PRODUCTION_OPERATOR_TOKEN_FILE",
        "/run/secrets/production_operator_token",
    )
    try:
        expected = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        raise HTTPException(503, "production_operator_identity_unavailable")
    if (
        len(expected.encode()) < 32
        or not secrets.compare_digest(x_production_operator_token, expected)
        or not OPERATOR_ID.fullmatch(x_operator_id)
    ):
        raise HTTPException(403, "production_operator_denied")
    return x_operator_id


def _operator(
    x_production_operator_token: str = Header(...),
    x_operator_id: str = Header(...),
    x_correlation_id: str = Header(...),
    idempotency_key: str = Header(...),
) -> Operator:
    actor = _authorized_actor(x_production_operator_token, x_operator_id)
    bounded_reference(x_correlation_id, "correlation_id", 36)
    bounded_reference(idempotency_key, "idempotency_key", 180)
    return Operator(actor, x_correlation_id, idempotency_key)


def _operator_reader(
    x_production_operator_token: str = Header(...),
    x_operator_id: str = Header(...),
) -> str:
    return _authorized_actor(x_production_operator_token, x_operator_id)


def _semantic(body: BaseModel | None, resource_id: str | None = None) -> str:
    value = {
        "resource_id": resource_id,
        "request": body.model_dump(mode="json") if body is not None else {},
    }
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _replay(
    db: Session,
    *,
    tenant_id: str,
    operator: Operator,
    resource: str,
    action: str,
    semantic: str,
) -> dict | None:
    row = db.scalar(
        select(CommandIdempotency).where(
            CommandIdempotency.tenant_id == tenant_id,
            CommandIdempotency.caller_identity == "production-operator:" + operator.actor,
            CommandIdempotency.resource == resource,
            CommandIdempotency.action == action,
            CommandIdempotency.api_version == "v1",
            CommandIdempotency.idempotency_key == operator.idempotency_key,
        )
    )
    if row is None:
        return None
    if row.semantic_sha256 != semantic:
        raise HTTPException(409, "idempotency_key_reused_with_different_request")
    if not isinstance(row.response_json, dict):
        raise HTTPException(503, "idempotency_result_unavailable")
    return row.response_json


def _store_mutation(
    db: Session,
    *,
    tenant_id: str,
    operator: Operator,
    resource: str,
    action: str,
    semantic: str,
    result: dict,
) -> None:
    db.add(
        CommandIdempotency(
            tenant_id=tenant_id,
            caller_identity="production-operator:" + operator.actor,
            resource=resource,
            action=action,
            api_version="v1",
            idempotency_key=operator.idempotency_key,
            semantic_sha256=semantic,
            status_code=200,
            resource_id=result.get("id"),
            response_json=result,
            response_ciphertext=None,
        )
    )


def _policy_json(policy: SmsDeliveryPolicy) -> dict:
    return {
        "id": policy.id,
        "tenant_id": policy.tenant_id,
        "environment": policy.environment,
        "policy_version": policy.policy_version,
        "enabled": policy.enabled,
        "mode": policy.mode,
        "authorization_id": policy.authorization_id,
        "authorization_change_id": policy.authorization_change_id,
        "approved_senders": policy.approved_senders,
        "approved_destinations": policy.approved_destinations,
        "recipient_scope": policy.recipient_scope,
        "transaction_categories": policy.transaction_categories,
        "per_minute_segments": policy.per_minute_segments,
        "per_hour_segments": policy.per_hour_segments,
        "per_day_segments": policy.per_day_segments,
        "provider_id": policy.provider_id,
        "billing_account_id": policy.billing_account_id,
        "max_total_spend_minor": policy.max_total_spend_minor,
        "spending_currency": policy.spending_currency,
        "account_grain": policy.account_grain,
        "valid_from": policy.valid_from.isoformat() if policy.valid_from else None,
        "valid_until": policy.valid_until.isoformat() if policy.valid_until else None,
        "approved_by": policy.approved_by,
        "activated_by": policy.activated_by,
        "system_kill_switch": policy.system_kill_switch,
        "tenant_kill_switch": policy.tenant_kill_switch,
        "sender_kill_switches": policy.sender_kill_switches,
        "reason": policy.reason,
        "updated_at": policy.updated_at.isoformat(),
    }


def _audit(
    db: Session,
    *,
    tenant_id: str | None,
    operator: Operator,
    action: str,
    target: str,
    before: dict,
    after: dict,
) -> None:
    db.add(
        Audit(
            tenant_id=tenant_id,
            actor=operator.actor,
            action=action,
            target=target,
            correlation_id=operator.correlation_id,
            before=before,
            after=after,
        )
    )


def _authorization_json(row: SmsProductionAuthorization) -> dict:
    return {
        "id": row.id,
        "change_id": row.change_id,
        "tenant_id": row.tenant_id,
        "environment": row.environment,
        "production_owner": row.production_owner,
        "approved_senders": row.approved_senders,
        "approved_destinations": row.approved_destinations,
        "approved_categories": row.approved_categories,
        "per_minute_segments": row.per_minute_segments,
        "per_hour_segments": row.per_hour_segments,
        "per_day_segments": row.per_day_segments,
        "provider_id": row.provider_id,
        "billing_account_id": row.billing_account_id,
        "max_total_spend_minor": row.max_total_spend_minor,
        "spending_currency": row.spending_currency,
        "account_grain": row.account_grain,
        "valid_from": row.valid_from.isoformat(),
        "valid_until": row.valid_until.isoformat(),
        "monitoring_owner": row.monitoring_owner,
        "escalation_owner": row.escalation_owner,
        "rollback_owner": row.rollback_owner,
        "kill_switch_procedure": row.kill_switch_procedure,
        "approved_release_sha": row.approved_release_sha,
        "approved_by": row.approved_by,
        "authorization_timestamp": row.authorization_timestamp.isoformat(),
        "review_at": row.review_at.isoformat(),
        "reason": row.reason,
    }


def _require_didww_scope(db: Session, body: AuthorizationIn) -> None:
    tenant = db.get(Tenant, body.tenant_id)
    account = db.get(BillingAccount, body.billing_account_id)
    provider = db.get(Provider, body.provider_id)
    if tenant is None or tenant.status != "active":
        raise HTTPException(409, "authorized_tenant_not_active")
    if account is None or account.tenant_id != tenant.id:
        raise HTTPException(409, "authorized_billing_account_mismatch")
    if (
        provider is None
        or provider.environment != "production"
        or provider.state != "enabled"
        or not provider.routing_enabled
        or provider.circuit_state != "closed"
        or provider.adapter_type != "jasmin_http"
        or not provider.credential_reference
        or not provider.dlr_source_key_id
        or str(provider.capabilities.get("carrier", "")).casefold() != "didww"
    ):
        raise HTTPException(409, "didww_provider_not_certified")
    senders = db.scalars(
        select(Sender).where(
            Sender.tenant_id == tenant.id,
            Sender.sender.in_(body.approved_senders),
            Sender.status == "approved",
        )
    ).all()
    if sorted(item.sender for item in senders) != sorted(body.approved_senders):
        raise HTTPException(409, "authorized_sender_not_approved")
    routes = db.scalars(
        select(Route).where(
            Route.provider_id == provider.id,
            Route.enabled == True,
            (Route.tenant_id == None) | (Route.tenant_id == tenant.id),
        )
    ).all()
    for destination in body.approved_destinations:
        matches = [route for route in routes if destination.startswith(route.prefix)]
        if not matches:
            raise HTTPException(409, "authorized_destination_has_no_didww_route")
        selected = max(matches, key=lambda route: (len(route.prefix), route.priority))
        if selected.country == "US":
            raise HTTPException(409, "usa_destinations_prohibited")


@router.post("/authorizations", status_code=201)
def authorize_production(
    body: AuthorizationIn,
    response: Response,
    operator: Operator = Depends(_operator),
    db: Session = Depends(session),
):
    semantic = _semantic(body)
    replay = _replay(
        db,
        tenant_id=body.tenant_id,
        operator=operator,
        resource="sms_production_authorization",
        action="authorize",
        semantic=semantic,
    )
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    if os.environ.get("SOURCE_SHA", "") != body.approved_release_sha:
        raise HTTPException(409, "authorization_release_sha_mismatch")
    _require_didww_scope(db, body)
    if db.scalar(
        select(SmsProductionAuthorization.id).where(
            SmsProductionAuthorization.change_id == body.change_id
        )
    ):
        raise HTTPException(409, "authorization_change_id_exists")
    now = datetime.now(timezone.utc)
    row = SmsProductionAuthorization(
        **body.model_dump(),
        environment="production",
        authorization_timestamp=now,
        idempotency_key=operator.idempotency_key,
        request_sha256=semantic,
    )
    db.add(row)
    db.flush()
    result = _authorization_json(row)
    _audit(
        db,
        tenant_id=row.tenant_id,
        operator=operator,
        action="sms.production.authorized",
        target=row.id,
        before={},
        after=result,
    )
    _store_mutation(
        db,
        tenant_id=row.tenant_id,
        operator=operator,
        resource="sms_production_authorization",
        action="authorize",
        semantic=semantic,
        result=result,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        replay = _replay(
            db,
            tenant_id=body.tenant_id,
            operator=operator,
            resource="sms_production_authorization",
            action="authorize",
            semantic=semantic,
        )
        if replay is None:
            raise HTTPException(409, "production_authorization_conflict")
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    response.headers["Idempotency-Replayed"] = "false"
    return result


@router.get("/authorizations/{authorization_id}")
def read_authorization(
    authorization_id: str,
    _operator_identity: str = Depends(_operator_reader),
    db: Session = Depends(session),
):
    row = db.get(SmsProductionAuthorization, authorization_id)
    if row is None:
        raise HTTPException(404, "production_authorization_not_found")
    result = _authorization_json(row)
    result["revoked"] = bool(
        db.scalar(
            select(SmsProductionAuthorizationRevocation.id).where(
                SmsProductionAuthorizationRevocation.authorization_id == row.id
            )
        )
    )
    return result


def _effective_authorization(
    db: Session, tenant_id: str, authorization_id: str
) -> SmsProductionAuthorization:
    row = db.get(SmsProductionAuthorization, authorization_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(404, "production_authorization_not_found")
    if db.scalar(
        select(SmsProductionAuthorizationRevocation.id).where(
            SmsProductionAuthorizationRevocation.authorization_id == row.id
        )
    ):
        raise HTTPException(409, "production_authorization_revoked")
    now = datetime.now(timezone.utc)
    valid_from = (
        row.valid_from if row.valid_from.tzinfo else row.valid_from.replace(tzinfo=timezone.utc)
    )
    valid_until = (
        row.valid_until if row.valid_until.tzinfo else row.valid_until.replace(tzinfo=timezone.utc)
    )
    review_at = (
        row.review_at if row.review_at.tzinfo else row.review_at.replace(tzinfo=timezone.utc)
    )
    if not valid_from <= now < valid_until or now >= review_at:
        raise HTTPException(409, "production_authorization_not_effective")
    if os.environ.get("SOURCE_SHA", "") != row.approved_release_sha:
        raise HTTPException(409, "authorization_release_sha_mismatch")
    return row


@router.post("/authorizations/{authorization_id}/revoke")
def revoke_authorization(
    authorization_id: str,
    body: RevocationIn,
    response: Response,
    operator: Operator = Depends(_operator),
    db: Session = Depends(session),
):
    row = db.get(SmsProductionAuthorization, authorization_id)
    if row is None:
        raise HTTPException(404, "production_authorization_not_found")
    semantic = _semantic(body, authorization_id)
    replay = _replay(
        db,
        tenant_id=row.tenant_id,
        operator=operator,
        resource="sms_production_authorization",
        action="revoke",
        semantic=semantic,
    )
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    if db.scalar(
        select(SmsProductionAuthorizationRevocation.id).where(
            SmsProductionAuthorizationRevocation.authorization_id == row.id
        )
    ):
        raise HTTPException(409, "production_authorization_already_revoked")
    revocation = SmsProductionAuthorizationRevocation(
        authorization_id=row.id,
        idempotency_key=operator.idempotency_key,
        revoked_by=operator.actor,
        correlation_id=operator.correlation_id,
        reason=body.reason,
    )
    db.add(revocation)
    policy = db.scalar(
        select(SmsDeliveryPolicy)
        .where(SmsDeliveryPolicy.authorization_id == row.id)
        .with_for_update()
    )
    before = _policy_json(policy) if policy is not None else {}
    if policy is not None:
        policy.policy_version += 1
        policy.enabled = False
        policy.mode = "SAFE"
        policy.tenant_kill_switch = True
        policy.reason = body.reason
        policy.updated_at = datetime.now(timezone.utc)
    db.flush()
    result = {
        "id": revocation.id,
        "authorization_id": row.id,
        "tenant_id": row.tenant_id,
        "revoked": True,
        "revoked_at": revocation.created_at.isoformat(),
        "policy": _policy_json(policy) if policy is not None else None,
    }
    _audit(
        db,
        tenant_id=row.tenant_id,
        operator=operator,
        action="sms.production.authorization_revoked",
        target=row.id,
        before=before,
        after=result,
    )
    _store_mutation(
        db,
        tenant_id=row.tenant_id,
        operator=operator,
        resource="sms_production_authorization",
        action="revoke",
        semantic=semantic,
        result=result,
    )
    db.commit()
    response.headers["Idempotency-Replayed"] = "false"
    return result


@router.post("/delivery-policies/{tenant_id}/activate")
def activate_policy(
    tenant_id: str,
    body: ActivationIn,
    response: Response,
    operator: Operator = Depends(_operator),
    db: Session = Depends(session),
):
    bounded_reference(tenant_id, "tenant_id", 36)
    semantic = _semantic(body, tenant_id)
    replay = _replay(
        db,
        tenant_id=tenant_id,
        operator=operator,
        resource="sms_delivery_policy",
        action="activate",
        semantic=semantic,
    )
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    authorization = _effective_authorization(db, tenant_id, body.authorization_id)
    policy = db.scalar(
        select(SmsDeliveryPolicy)
        .where(
            SmsDeliveryPolicy.tenant_id == tenant_id,
            SmsDeliveryPolicy.environment == "production",
        )
        .with_for_update()
    )
    actual_version = policy.policy_version if policy is not None else 0
    if actual_version != body.expected_policy_version:
        raise HTTPException(409, "production_policy_version_conflict")
    if body.mode == "TRANSACTIONAL_CANARY":
        gate = db.scalar(
            select(SmsProductionCanaryGate).where(
                SmsProductionCanaryGate.allowed_tenant == tenant_id,
                SmsProductionCanaryGate.enabled == True,
            )
        )
        if gate is None:
            raise HTTPException(409, "production_canary_gate_missing")
    control = db.scalar(
        select(SmsSystemControl)
        .where(SmsSystemControl.environment == "production")
        .with_for_update()
    )
    if control is None or control.kill_switch:
        raise HTTPException(409, "production_system_kill_switch_engaged")
    before = _policy_json(policy) if policy is not None else {}
    if policy is None:
        policy = SmsDeliveryPolicy(tenant_id=tenant_id, environment="production")
        db.add(policy)
    policy.policy_version = actual_version + 1
    policy.enabled = True
    policy.mode = body.mode
    policy.authorization_id = authorization.id
    policy.authorization_change_id = authorization.change_id
    policy.approved_senders = authorization.approved_senders
    policy.approved_destinations = authorization.approved_destinations
    policy.recipient_scope = "exact_allowlist"
    policy.transaction_categories = authorization.approved_categories
    policy.per_minute_segments = authorization.per_minute_segments
    policy.per_hour_segments = authorization.per_hour_segments
    policy.per_day_segments = authorization.per_day_segments
    policy.provider_id = authorization.provider_id
    policy.billing_account_id = authorization.billing_account_id
    policy.max_total_spend_minor = authorization.max_total_spend_minor
    policy.spending_currency = authorization.spending_currency
    policy.account_grain = authorization.account_grain
    policy.valid_from = authorization.valid_from
    policy.valid_until = authorization.valid_until
    policy.approved_by = authorization.approved_by
    policy.activated_by = operator.actor
    policy.system_kill_switch = False
    policy.tenant_kill_switch = False
    policy.sender_kill_switches = []
    policy.reason = body.reason
    policy.updated_at = datetime.now(timezone.utc)
    db.flush()
    result = _policy_json(policy)
    _audit(
        db,
        tenant_id=tenant_id,
        operator=operator,
        action="sms.transactional_production.activated",
        target=policy.id,
        before=before,
        after=result,
    )
    _store_mutation(
        db,
        tenant_id=tenant_id,
        operator=operator,
        resource="sms_delivery_policy",
        action="activate",
        semantic=semantic,
        result=result,
    )
    db.commit()
    response.headers["Idempotency-Replayed"] = "false"
    return result


@router.post("/delivery-policies/{tenant_id}/deactivate")
def deactivate_policy(
    tenant_id: str,
    body: PolicyMutationIn,
    response: Response,
    operator: Operator = Depends(_operator),
    db: Session = Depends(session),
):
    semantic = _semantic(body, tenant_id)
    replay = _replay(
        db,
        tenant_id=tenant_id,
        operator=operator,
        resource="sms_delivery_policy",
        action="deactivate",
        semantic=semantic,
    )
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    policy = db.scalar(
        select(SmsDeliveryPolicy).where(SmsDeliveryPolicy.tenant_id == tenant_id).with_for_update()
    )
    if policy is None:
        raise HTTPException(404, "production_policy_not_found")
    if policy.policy_version != body.expected_policy_version:
        raise HTTPException(409, "production_policy_version_conflict")
    before = _policy_json(policy)
    policy.policy_version += 1
    policy.enabled = False
    policy.mode = "SAFE"
    policy.tenant_kill_switch = True
    policy.reason = body.reason
    policy.updated_at = datetime.now(timezone.utc)
    result = _policy_json(policy)
    _audit(
        db,
        tenant_id=tenant_id,
        operator=operator,
        action="sms.transactional_production.deactivated",
        target=policy.id,
        before=before,
        after=result,
    )
    _store_mutation(
        db,
        tenant_id=tenant_id,
        operator=operator,
        resource="sms_delivery_policy",
        action="deactivate",
        semantic=semantic,
        result=result,
    )
    db.commit()
    response.headers["Idempotency-Replayed"] = "false"
    return result


def _kill_switch(
    *,
    db: Session,
    tenant_id: str,
    body: PolicyMutationIn,
    operator: Operator,
    engaged: bool,
) -> dict:
    policy = db.scalar(
        select(SmsDeliveryPolicy).where(SmsDeliveryPolicy.tenant_id == tenant_id).with_for_update()
    )
    if policy is None:
        raise HTTPException(404, "production_policy_not_found")
    if policy.policy_version != body.expected_policy_version:
        raise HTTPException(409, "production_policy_version_conflict")
    before = _policy_json(policy)
    policy.policy_version += 1
    policy.tenant_kill_switch = engaged
    policy.reason = body.reason
    policy.updated_at = datetime.now(timezone.utc)
    result = _policy_json(policy)
    _audit(
        db,
        tenant_id=tenant_id,
        operator=operator,
        action="sms.kill_switch.engaged" if engaged else "sms.kill_switch.reset",
        target=policy.id,
        before=before,
        after=result,
    )
    return result


@router.post("/delivery-policies/{tenant_id}/kill-switch/{action}")
def tenant_kill_switch(
    tenant_id: str,
    action: Literal["engage", "reset"],
    body: PolicyMutationIn,
    response: Response,
    operator: Operator = Depends(_operator),
    db: Session = Depends(session),
):
    semantic = _semantic(body, tenant_id + ":" + action)
    replay = _replay(
        db,
        tenant_id=tenant_id,
        operator=operator,
        resource="sms_delivery_policy",
        action="kill_switch_" + action,
        semantic=semantic,
    )
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    result = _kill_switch(
        db=db,
        tenant_id=tenant_id,
        body=body,
        operator=operator,
        engaged=action == "engage",
    )
    _store_mutation(
        db,
        tenant_id=tenant_id,
        operator=operator,
        resource="sms_delivery_policy",
        action="kill_switch_" + action,
        semantic=semantic,
        result=result,
    )
    db.commit()
    response.headers["Idempotency-Replayed"] = "false"
    return result


@router.post("/delivery-policies/{tenant_id}/sender-kill-switch/{action}")
def sender_kill_switch(
    tenant_id: str,
    action: Literal["engage", "reset"],
    body: SenderKillSwitchIn,
    response: Response,
    operator: Operator = Depends(_operator),
    db: Session = Depends(session),
):
    semantic = _semantic(body, tenant_id + ":" + action)
    replay = _replay(
        db,
        tenant_id=tenant_id,
        operator=operator,
        resource="sms_sender_kill_switch",
        action=action,
        semantic=semantic,
    )
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    policy = db.scalar(
        select(SmsDeliveryPolicy).where(SmsDeliveryPolicy.tenant_id == tenant_id).with_for_update()
    )
    if policy is None:
        raise HTTPException(404, "production_policy_not_found")
    if policy.policy_version != body.expected_policy_version:
        raise HTTPException(409, "production_policy_version_conflict")
    before = _policy_json(policy)
    blocked = set(policy.sender_kill_switches or [])
    if action == "engage":
        blocked.add(body.sender)
    else:
        blocked.discard(body.sender)
    policy.sender_kill_switches = sorted(blocked)
    policy.policy_version += 1
    policy.reason = body.reason
    policy.updated_at = datetime.now(timezone.utc)
    result = _policy_json(policy)
    _audit(
        db,
        tenant_id=tenant_id,
        operator=operator,
        action="sms.sender_kill_switch." + action,
        target=policy.id,
        before=before,
        after=result,
    )
    _store_mutation(
        db,
        tenant_id=tenant_id,
        operator=operator,
        resource="sms_sender_kill_switch",
        action=action,
        semantic=semantic,
        result=result,
    )
    db.commit()
    response.headers["Idempotency-Replayed"] = "false"
    return result


@router.post("/system-kill-switch/{action}")
def system_kill_switch(
    action: Literal["engage", "reset"],
    body: SystemKillSwitchIn,
    response: Response,
    operator: Operator = Depends(_operator),
    db: Session = Depends(session),
):
    semantic = _semantic(body, "production:" + action)
    replay = _replay(
        db,
        tenant_id="system",
        operator=operator,
        resource="sms_system_kill_switch",
        action=action,
        semantic=semantic,
    )
    if replay is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return replay
    control = db.scalar(
        select(SmsSystemControl)
        .where(SmsSystemControl.environment == "production")
        .with_for_update()
    )
    actual_version = control.control_version if control is not None else 0
    if action == "reset" and actual_version != body.expected_control_version:
        raise HTTPException(409, "system_control_version_conflict")
    before = (
        {
            "control_version": control.control_version,
            "kill_switch": control.kill_switch,
            "reason": control.reason,
        }
        if control is not None
        else {}
    )
    if control is None:
        control = SmsSystemControl(environment="production")
        db.add(control)
    control.control_version = actual_version + 1
    control.kill_switch = action == "engage"
    control.reason = body.reason
    control.actor = operator.actor
    control.updated_at = datetime.now(timezone.utc)
    policies = db.scalars(select(SmsDeliveryPolicy).with_for_update()).all()
    for policy in policies:
        policy.system_kill_switch = control.kill_switch
        policy.policy_version += 1
        policy.updated_at = control.updated_at
    db.flush()
    result = {
        "id": control.id,
        "environment": "production",
        "control_version": control.control_version,
        "kill_switch": control.kill_switch,
        "affected_policies": len(policies),
        "updated_at": control.updated_at.isoformat(),
    }
    _audit(
        db,
        tenant_id=None,
        operator=operator,
        action="sms.system_kill_switch." + action,
        target=control.id,
        before=before,
        after=result,
    )
    _store_mutation(
        db,
        tenant_id="system",
        operator=operator,
        resource="sms_system_kill_switch",
        action=action,
        semantic=semantic,
        result=result,
    )
    db.commit()
    response.headers["Idempotency-Replayed"] = "false"
    return result


@router.get("/delivery-policies/{tenant_id}")
def read_policy(
    tenant_id: str,
    _operator_identity: str = Depends(_operator_reader),
    db: Session = Depends(session),
):
    policy = db.scalar(select(SmsDeliveryPolicy).where(SmsDeliveryPolicy.tenant_id == tenant_id))
    if policy is None:
        return {
            "tenant_id": tenant_id,
            "environment": "production",
            "enabled": False,
            "mode": "SAFE",
            "status": "NOT_AUTHORIZED",
        }
    result = _policy_json(policy)
    result["authorization_revoked"] = bool(
        policy.authorization_id
        and db.scalar(
            select(SmsProductionAuthorizationRevocation.id).where(
                SmsProductionAuthorizationRevocation.authorization_id == policy.authorization_id
            )
        )
    )
    return result
