"""Canonical, tenant-scoped Telnexa v1 control and messaging API."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import session
from .models import (
    ApiKey,
    Audit,
    BillingAccount,
    CommandIdempotency,
    Message,
    MessageEvent,
    PhoneNumber,
    Provider,
    Route,
    ServiceAccount,
    SmppCredential,
    SmsDispatchAttempt,
    SmsDispatchJob,
    SmsProviderEventInbox,
    SmsReconciliationCase,
    Tenant,
)
from .schemas import SendRequest

router = APIRouter(prefix="/api/v1", tags=["Canonical Telnexa API"])
IDEMPOTENCY_REPLAY_HEADER = {
    "Idempotency-Replayed": {
        "description": "True when the durable original result was returned.",
        "schema": {"type": "string", "enum": ["true", "false"]},
    }
}


def now() -> datetime:
    return datetime.now(timezone.utc)


class ServiceAccountIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    scopes: list[str] = Field(min_length=1, max_length=40)


class ApiKeyIn(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    scopes: list[str] = Field(min_length=1, max_length=40)
    account_id: Optional[str] = None


class SmppIn(BaseModel):
    system_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{3,32}$")
    bind_mode: str = Field(default="transceiver", pattern="^(transmitter|receiver|transceiver)$")
    max_binds: int = Field(default=1, ge=1, le=20)
    tps: int = Field(default=1, ge=1, le=1000)
    ip_allowlist: list[str] = Field(default_factory=list, max_length=50)


class SmppPatch(BaseModel):
    bind_mode: Optional[str] = Field(default=None, pattern="^(transmitter|receiver|transceiver)$")
    max_binds: Optional[int] = Field(default=None, ge=1, le=20)
    tps: Optional[int] = Field(default=None, ge=1, le=1000)
    ip_allowlist: Optional[list[str]] = Field(default=None, max_length=50)
    enabled: Optional[bool] = None


def _auth(required: str = "read"):
    from .app import authn

    return authn(required)


def _tenant_item(s: Session, model: Any, item_id: str, tenant_id: str) -> Any:
    item = s.scalar(select(model).where(model.id == item_id, model.tenant_id == tenant_id))
    if item is None:
        raise HTTPException(404, "not_found")
    return item


def _message_json(item: Message) -> dict[str, Any]:
    from .app import message_json

    return message_json(item)


def _audit(
    s: Session,
    tenant_id: str,
    action: str,
    target: str,
    correlation_id: str,
    actor: str = "tenant_api",
) -> None:
    s.add(
        Audit(
            tenant_id=tenant_id,
            actor=actor[:80],
            action=action,
            target=target,
            correlation_id=correlation_id[:36],
            before={},
            after={},
        )
    )


def _caller_identity(tenant_id: str) -> str:
    identity = getattr(tenant_id, "caller_identity", "")
    if not identity:
        raise HTTPException(500, "authenticated_caller_identity_missing")
    return identity


def _semantic_sha256(
    resource_id: Optional[str], body: BaseModel, *, partial_update: bool = False
) -> str:
    semantic = {
        "resource_id": resource_id,
        "request": body.model_dump(mode="json", exclude_unset=partial_update),
    }
    return hashlib.sha256(
        json.dumps(semantic, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _response_fernet() -> Fernet:
    secret = os.environ.get("BILLING_JWT_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "idempotency_response_encryption_unavailable")
    key = hashlib.sha256(("telnexa-command-idempotency-v1:" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _encrypted_response(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return _response_fernet().encrypt(encoded).decode()


def _stored_response(record: CommandIdempotency) -> dict[str, Any]:
    if record.response_json is not None:
        return record.response_json
    if not record.response_ciphertext:
        raise HTTPException(503, "idempotency_result_incomplete")
    try:
        value = _response_fernet().decrypt(record.response_ciphertext.encode())
        result = json.loads(value)
    except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(503, "idempotency_result_unavailable")
    if not isinstance(result, dict):
        raise HTTPException(503, "idempotency_result_invalid")
    return result


def _existing_command(
    s: Session,
    *,
    tenant_id: str,
    caller_identity: str,
    resource: str,
    action: str,
    idempotency_key: str,
    semantic_sha256: str,
) -> Optional[CommandIdempotency]:
    record = s.scalar(
        select(CommandIdempotency).where(
            CommandIdempotency.tenant_id == tenant_id,
            CommandIdempotency.caller_identity == caller_identity,
            CommandIdempotency.resource == resource,
            CommandIdempotency.action == action,
            CommandIdempotency.api_version == "v1",
            CommandIdempotency.idempotency_key == idempotency_key,
        )
    )
    if record is not None and record.semantic_sha256 != semantic_sha256:
        raise HTTPException(409, "idempotency_key_reused_with_different_request")
    return record


def _command_record(
    *,
    tenant_id: str,
    caller_identity: str,
    resource: str,
    action: str,
    idempotency_key: str,
    semantic_sha256: str,
    status_code: int,
    resource_id: str,
    response_json: Optional[dict[str, Any]] = None,
    response_ciphertext: Optional[str] = None,
) -> CommandIdempotency:
    return CommandIdempotency(
        tenant_id=tenant_id,
        caller_identity=caller_identity,
        resource=resource,
        action=action,
        api_version="v1",
        idempotency_key=idempotency_key,
        semantic_sha256=semantic_sha256,
        status_code=status_code,
        resource_id=resource_id,
        response_json=response_json,
        response_ciphertext=response_ciphertext,
    )


def _replay_after_conflict(
    s: Session,
    *,
    tenant_id: str,
    caller_identity: str,
    resource: str,
    action: str,
    idempotency_key: str,
    semantic_sha256: str,
) -> dict[str, Any]:
    s.rollback()
    record = _existing_command(
        s,
        tenant_id=tenant_id,
        caller_identity=caller_identity,
        resource=resource,
        action=action,
        idempotency_key=idempotency_key,
        semantic_sha256=semantic_sha256,
    )
    if record is None:
        raise HTTPException(409, "command_conflict")
    return _stored_response(record)


@router.get("/me")
def me(tenant_id: str = Depends(_auth()), s: Session = Depends(session)) -> dict[str, Any]:
    tenant = s.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(404, "tenant_not_found")
    return {"tenant_id": tenant.id, "name": tenant.name, "status": tenant.status}


@router.get("/accounts")
def accounts(tenant_id: str = Depends(_auth()), s: Session = Depends(session)) -> dict[str, Any]:
    return {
        "items": s.scalars(
            select(BillingAccount).where(BillingAccount.tenant_id == tenant_id)
        ).all()
    }


@router.get("/accounts/{account_id}")
def account(
    account_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> Any:
    return _tenant_item(s, BillingAccount, account_id, tenant_id)


@router.get("/tenants")
def tenants(tenant_id: str = Depends(_auth()), s: Session = Depends(session)) -> dict[str, Any]:
    tenant = s.get(Tenant, tenant_id)
    return {"items": [tenant] if tenant else []}


@router.get("/tenants/{requested_tenant_id}")
def tenant_detail(
    requested_tenant_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> Any:
    if requested_tenant_id != tenant_id:
        raise HTTPException(404, "not_found")
    item = s.get(Tenant, tenant_id)
    if item is None:
        raise HTTPException(404, "not_found")
    return item


@router.get("/service-accounts")
def service_accounts(
    tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    rows = s.scalars(
        select(ServiceAccount)
        .where(ServiceAccount.tenant_id == tenant_id)
        .order_by(ServiceAccount.created_at.desc())
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "client_id": row.client_id,
                "scopes": row.scopes.split(),
                "enabled": row.enabled,
                "created_at": row.created_at,
                "rotated_at": row.rotated_at,
            }
            for row in rows
        ]
    }


@router.post("/service-accounts", status_code=201)
def service_account_create(
    body: ServiceAccountIn, tenant_id: str = Depends(_auth("admin")), s: Session = Depends(session)
) -> dict[str, Any]:
    from .app import ph

    raw = "tnxs_" + secrets.token_urlsafe(36)
    item = ServiceAccount(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        name=body.name,
        client_id="tnx-" + secrets.token_hex(12),
        secret_hash=ph.hash(raw),
        scopes=" ".join(sorted(set(body.scopes))),
    )
    s.add(item)
    _audit(s, tenant_id, "service_account.created", item.id, item.id)
    s.commit()
    return {
        "id": item.id,
        "client_id": item.client_id,
        "client_secret": raw,
        "scopes": item.scopes.split(),
    }


@router.get("/api-keys")
def api_keys(tenant_id: str = Depends(_auth()), s: Session = Depends(session)) -> dict[str, Any]:
    rows = s.scalars(
        select(ApiKey).where(ApiKey.tenant_id == tenant_id).order_by(ApiKey.created_at.desc())
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "prefix": row.prefix,
                "scopes": row.scopes.split(),
                "revoked": row.revoked,
                "created_at": row.created_at,
                "last_used_at": row.last_used_at,
            }
            for row in rows
        ]
    }


@router.post("/api-keys", status_code=201)
def api_key_create(
    body: ApiKeyIn, tenant_id: str = Depends(_auth("admin")), s: Session = Depends(session)
) -> dict[str, Any]:
    from .app import ph

    raw = "tnx_" + secrets.token_urlsafe(36)
    item = ApiKey(
        tenant_id=tenant_id,
        account_id=body.account_id,
        prefix=raw[:12],
        secret_hash=ph.hash(raw),
        scopes=" ".join(sorted(set(body.scopes))),
    )
    s.add(item)
    s.flush()
    _audit(s, tenant_id, "api_key.created", item.id, item.id)
    s.commit()
    return {"id": item.id, "api_key": raw, "prefix": item.prefix, "scopes": item.scopes.split()}


@router.post("/sms/messages", status_code=202)
def sms_send(
    body: SendRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=180),
    x_correlation_id: str = Header(alias="X-Correlation-ID", min_length=8, max_length=200),
    tenant_id: str = Depends(_auth("messages:write")),
    s: Session = Depends(session),
) -> dict[str, Any]:
    from .app import send

    return send(body, idempotency_key, x_correlation_id, tenant_id, s)


@router.get("/sms/messages")
def sms_messages(
    tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    rows = s.scalars(
        select(Message)
        .where(Message.tenant_id == tenant_id)
        .order_by(Message.created_at.desc())
        .limit(500)
    ).all()
    return {"items": [_message_json(item) for item in rows]}


@router.get("/sms/messages/{message_id}")
def sms_message(
    message_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    return _message_json(_tenant_item(s, Message, message_id, tenant_id))


@router.post("/sms/messages/{message_id}/cancel")
def sms_cancel(
    message_id: str,
    tenant_id: str = Depends(_auth("messages:write")),
    s: Session = Depends(session),
) -> dict[str, Any]:
    item = s.scalar(
        select(Message)
        .where(Message.id == message_id, Message.tenant_id == tenant_id)
        .with_for_update()
    )
    if item is None:
        raise HTTPException(404, "message_not_found")
    if item.status not in {"accepted", "queued"}:
        raise HTTPException(409, "message_not_cancellable")
    job = s.scalar(
        select(SmsDispatchJob)
        .where(SmsDispatchJob.message_id == item.id, SmsDispatchJob.tenant_id == tenant_id)
        .with_for_update()
    )
    if job and job.state not in {"queued", "retry"}:
        raise HTTPException(409, "provider_submission_requires_reconciliation")
    item.status = "cancelled"
    item.terminal_at = now()
    if job:
        job.state = "cancelled"
        job.updated_at = now()
    _audit(s, tenant_id, "sms.message.cancelled", item.id, item.correlation_id)
    s.commit()
    return _message_json(item)


@router.get("/sms/delivery-reports")
def delivery_reports(
    tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    rows = s.scalars(
        select(MessageEvent)
        .where(MessageEvent.tenant_id == tenant_id)
        .order_by(MessageEvent.occurred_at.desc())
        .limit(500)
    ).all()
    return {"items": rows}


@router.get("/sms/providers")
def sms_providers(
    tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    del tenant_id
    rows = s.scalars(
        select(Provider).where(Provider.routing_enabled == True).order_by(Provider.name)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "name": row.name,
                "state": row.state,
                "circuit_state": row.circuit_state,
                "health_score": row.health_score,
                "capabilities": row.capabilities,
            }
            for row in rows
        ]
    }


@router.get("/sms/providers/{provider_id}/health")
def sms_provider_health(
    provider_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    del tenant_id
    item = s.get(Provider, provider_id)
    if item is None:
        raise HTTPException(404, "provider_not_found")
    return {
        "provider_id": item.id,
        "status": "ok"
        if item.state == "enabled" and item.circuit_state == "closed"
        else "degraded",
        "health_score": item.health_score,
        "circuit_state": item.circuit_state,
        "last_success_at": item.last_success_at,
        "last_failure_at": item.last_failure_at,
    }


@router.get("/smpp/accounts")
def smpp_accounts(
    tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    rows = s.scalars(
        select(SmppCredential)
        .where(SmppCredential.tenant_id == tenant_id)
        .order_by(SmppCredential.created_at.desc())
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "system_id": row.system_id,
                "bind_mode": row.bind_mode,
                "max_binds": row.max_binds,
                "tps": row.tps,
                "ip_allowlist": row.ip_allowlist,
                "enabled": row.enabled,
            }
            for row in rows
        ]
    }


@router.post(
    "/smpp/accounts",
    status_code=201,
    responses={201: {"headers": IDEMPOTENCY_REPLAY_HEADER}},
)
def smpp_create(
    body: SmppIn,
    response: Response,
    idempotency_key: str = Header(..., min_length=8, max_length=180, pattern=r"^[A-Za-z0-9._:-]+$"),
    x_correlation_id: str = Header(
        ..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    ),
    tenant_id: str = Depends(_auth("admin")),
    s: Session = Depends(session),
) -> dict[str, Any]:
    from .app import ph

    caller_identity = _caller_identity(tenant_id)
    semantic_sha256 = _semantic_sha256(None, body)
    prior = _existing_command(
        s,
        tenant_id=tenant_id,
        caller_identity=caller_identity,
        resource="smpp_accounts",
        action="create",
        idempotency_key=idempotency_key,
        semantic_sha256=semantic_sha256,
    )
    if prior is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return _stored_response(prior)
    raw = secrets.token_urlsafe(24)
    item = SmppCredential(
        id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        system_id=body.system_id,
        password_hash=ph.hash(raw),
        bind_mode=body.bind_mode,
        max_binds=body.max_binds,
        tps=body.tps,
        ip_allowlist=body.ip_allowlist,
        enabled=False,
    )
    result = {"id": item.id, "system_id": item.system_id, "password": raw, "enabled": item.enabled}
    s.add(item)
    _audit(
        s,
        tenant_id,
        "smpp.account.created",
        item.id,
        x_correlation_id,
        actor=caller_identity,
    )
    s.add(
        _command_record(
            tenant_id=tenant_id,
            caller_identity=caller_identity,
            resource="smpp_accounts",
            action="create",
            idempotency_key=idempotency_key,
            semantic_sha256=semantic_sha256,
            status_code=201,
            resource_id=item.id,
            response_ciphertext=_encrypted_response(result),
        )
    )
    try:
        s.commit()
    except IntegrityError:
        result = _replay_after_conflict(
            s,
            tenant_id=tenant_id,
            caller_identity=caller_identity,
            resource="smpp_accounts",
            action="create",
            idempotency_key=idempotency_key,
            semantic_sha256=semantic_sha256,
        )
        response.headers["Idempotency-Replayed"] = "true"
        return result
    response.headers["Idempotency-Replayed"] = "false"
    return result


@router.get("/smpp/accounts/{account_id}")
def smpp_detail(
    account_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    item = _tenant_item(s, SmppCredential, account_id, tenant_id)
    return {
        "id": item.id,
        "system_id": item.system_id,
        "bind_mode": item.bind_mode,
        "max_binds": item.max_binds,
        "tps": item.tps,
        "ip_allowlist": item.ip_allowlist,
        "enabled": item.enabled,
    }


@router.patch(
    "/smpp/accounts/{account_id}",
    responses={200: {"headers": IDEMPOTENCY_REPLAY_HEADER}},
)
def smpp_patch(
    account_id: str,
    body: SmppPatch,
    response: Response,
    idempotency_key: str = Header(..., min_length=8, max_length=180, pattern=r"^[A-Za-z0-9._:-]+$"),
    x_correlation_id: str = Header(
        ..., min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$"
    ),
    tenant_id: str = Depends(_auth("admin")),
    s: Session = Depends(session),
) -> dict[str, Any]:
    caller_identity = _caller_identity(tenant_id)
    semantic_sha256 = _semantic_sha256(account_id, body, partial_update=True)
    resource = f"smpp_accounts:{account_id}"
    prior = _existing_command(
        s,
        tenant_id=tenant_id,
        caller_identity=caller_identity,
        resource=resource,
        action="update",
        idempotency_key=idempotency_key,
        semantic_sha256=semantic_sha256,
    )
    if prior is not None:
        response.headers["Idempotency-Replayed"] = "true"
        return _stored_response(prior)
    item = _tenant_item(s, SmppCredential, account_id, tenant_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(item, key, value)
    result = {
        "id": item.id,
        "system_id": item.system_id,
        "bind_mode": item.bind_mode,
        "max_binds": item.max_binds,
        "tps": item.tps,
        "ip_allowlist": item.ip_allowlist,
        "enabled": item.enabled,
    }
    _audit(
        s,
        tenant_id,
        "smpp.account.updated",
        item.id,
        x_correlation_id,
        actor=caller_identity,
    )
    s.add(
        _command_record(
            tenant_id=tenant_id,
            caller_identity=caller_identity,
            resource=resource,
            action="update",
            idempotency_key=idempotency_key,
            semantic_sha256=semantic_sha256,
            status_code=200,
            resource_id=item.id,
            response_json=result,
        )
    )
    try:
        s.commit()
    except IntegrityError:
        result = _replay_after_conflict(
            s,
            tenant_id=tenant_id,
            caller_identity=caller_identity,
            resource=resource,
            action="update",
            idempotency_key=idempotency_key,
            semantic_sha256=semantic_sha256,
        )
        response.headers["Idempotency-Replayed"] = "true"
        return result
    response.headers["Idempotency-Replayed"] = "false"
    return result


@router.get("/messaging/messages")
def messaging_messages(
    tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    return sms_messages(tenant_id, s)


@router.get("/dids")
def dids(tenant_id: str = Depends(_auth()), s: Session = Depends(session)) -> dict[str, Any]:
    return {
        "items": s.scalars(
            select(PhoneNumber)
            .where(PhoneNumber.tenant_id == tenant_id)
            .order_by(PhoneNumber.number)
        ).all()
    }


@router.get("/dids/{did_id}")
def did_detail(
    did_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> Any:
    return _tenant_item(s, PhoneNumber, did_id, tenant_id)


@router.get("/routing/routes")
def routes(tenant_id: str = Depends(_auth()), s: Session = Depends(session)) -> dict[str, Any]:
    rows = s.scalars(
        select(Route)
        .where((Route.tenant_id == tenant_id) | (Route.tenant_id == None), Route.enabled == True)
        .order_by(Route.priority.desc())
    ).all()
    return {"items": rows}


@router.get("/billing/account")
def billing_account(tenant_id: str = Depends(_auth()), s: Session = Depends(session)) -> Any:
    item = s.scalar(select(BillingAccount).where(BillingAccount.tenant_id == tenant_id))
    if item is None:
        raise HTTPException(404, "billing_account_not_found")
    return item


async def _ingest_provider_webhook(request: Request, provider: str, s: Session) -> dict[str, Any]:
    from .provider_events import ingest, verify_signature

    body = await request.body()
    if len(body) > 1048576:
        raise HTTPException(413, "provider_event_too_large")
    timestamp = request.headers.get("X-Telnexa-Timestamp", "")
    event_id = request.headers.get("X-Telnexa-Event-Id", "")
    signature = request.headers.get("X-Telnexa-Signature", "")
    key_id = request.headers.get("X-Key-ID", provider)
    try:
        secret = Path(os.environ["TELNEXA_PROVIDER_EVENT_HMAC_SECRET_FILE"]).read_bytes().strip()
    except (KeyError, OSError):
        raise HTTPException(503, "provider_event_identity_unavailable")
    if not verify_signature(secret, "POST", request.url.path, timestamp, event_id, body, signature):
        raise HTTPException(401, "invalid_provider_event_signature")
    try:
        payload = json.loads(body)
        row, duplicate = ingest(s, key_id, event_id, body, payload)
        s.commit()
    except (ValueError, json.JSONDecodeError) as exc:
        s.rollback()
        raise HTTPException(422, str(exc))
    return {
        "event_id": row.event_id,
        "accepted": True,
        "duplicate": duplicate,
        "payload_hash": row.payload_hash,
    }


@router.post("/webhooks/sms/delivery", status_code=202)
async def webhook_sms_delivery(request: Request, s: Session = Depends(session)) -> dict[str, Any]:
    return await _ingest_provider_webhook(request, "sms-delivery", s)


@router.post("/webhooks/provider/{provider}", status_code=202)
async def webhook_provider(
    provider: str, request: Request, s: Session = Depends(session)
) -> dict[str, Any]:
    if not provider or len(provider) > 80:
        raise HTTPException(404, "provider_not_found")
    return await _ingest_provider_webhook(request, provider, s)


@router.get("/delivery-reports")
def canonical_delivery_reports(
    tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    return delivery_reports(tenant_id, s)


def _operation(item: Message, job: Optional[SmsDispatchJob]) -> dict[str, Any]:
    mapping = {
        "accepted": "QUEUED",
        "queued": "QUEUED",
        "processing": "PROCESSING",
        "submitted": "PROCESSING",
        "delivered": "SUCCEEDED",
        "failed": "FAILED",
        "cancelled": "CANCELLED",
        "submission_unknown": "RECONCILIATION_REQUIRED",
    }
    status = mapping.get(item.status, item.status.upper())
    return {
        "operation_id": item.id,
        "status": status,
        "resource_version": item.updated_at.isoformat(),
        "result": _message_json(item),
        "error": item.failure_code,
        "retryability": status in {"FAILED", "RECONCILIATION_REQUIRED"},
        "reconciliation_required": status == "RECONCILIATION_REQUIRED",
        "correlation_id": item.correlation_id,
        "attempts": job.attempt_count if job else 0,
    }


@router.get("/operations")
def operations(tenant_id: str = Depends(_auth()), s: Session = Depends(session)) -> dict[str, Any]:
    rows = s.scalars(
        select(Message)
        .where(Message.tenant_id == tenant_id)
        .order_by(Message.created_at.desc())
        .limit(500)
    ).all()
    jobs = {
        job.message_id: job
        for job in s.scalars(
            select(SmsDispatchJob).where(SmsDispatchJob.tenant_id == tenant_id)
        ).all()
    }
    return {"items": [_operation(item, jobs.get(item.id)) for item in rows]}


@router.get("/operations/{operation_id}")
def operation_detail(
    operation_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    item = _tenant_item(s, Message, operation_id, tenant_id)
    job = s.scalar(
        select(SmsDispatchJob).where(
            SmsDispatchJob.message_id == item.id, SmsDispatchJob.tenant_id == tenant_id
        )
    )
    return _operation(item, job)


@router.get("/operations/{operation_id}/events")
def operation_events(
    operation_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    _tenant_item(s, Message, operation_id, tenant_id)
    rows = s.scalars(
        select(MessageEvent)
        .where(MessageEvent.message_id == operation_id, MessageEvent.tenant_id == tenant_id)
        .order_by(MessageEvent.occurred_at)
    ).all()
    return {"operation_id": operation_id, "items": rows}


@router.get("/operations/{operation_id}/attempts")
def operation_attempts(
    operation_id: str, tenant_id: str = Depends(_auth()), s: Session = Depends(session)
) -> dict[str, Any]:
    item = _tenant_item(s, Message, operation_id, tenant_id)
    rows = s.scalars(
        select(SmsDispatchAttempt)
        .where(SmsDispatchAttempt.message_id == item.id, SmsDispatchAttempt.tenant_id == tenant_id)
        .order_by(SmsDispatchAttempt.attempt_number)
    ).all()
    return {"operation_id": operation_id, "items": rows}


@router.post("/operations/{operation_id}/cancel")
def operation_cancel(
    operation_id: str,
    tenant_id: str = Depends(_auth("messages:write")),
    s: Session = Depends(session),
) -> dict[str, Any]:
    return sms_cancel(operation_id, tenant_id, s)


@router.post("/operations/{operation_id}/reconcile")
def operation_reconcile(
    operation_id: str,
    tenant_id: str = Depends(_auth("messages:write")),
    s: Session = Depends(session),
) -> dict[str, Any]:
    item = _tenant_item(s, Message, operation_id, tenant_id)
    if item.status != "submission_unknown":
        return operation_detail(operation_id, tenant_id, s)
    case = s.scalar(
        select(SmsReconciliationCase).where(
            SmsReconciliationCase.case_type == "submission_unknown",
            SmsReconciliationCase.reference_id == item.id,
        )
    )
    if case is None:
        case = SmsReconciliationCase(
            tenant_id=tenant_id,
            message_id=item.id,
            case_type="submission_unknown",
            reference_id=item.id,
            evidence={"submission_certainty": item.submission_certainty},
            resolution={},
        )
        s.add(case)
    case.updated_at = now()
    _audit(s, tenant_id, "operation.reconciliation_requested", item.id, item.correlation_id)
    s.commit()
    value = operation_detail(operation_id, tenant_id, s)
    value["reconciliation_case_id"] = case.id
    return value


@router.get("/audit")
def audit_log(
    tenant_id: str = Depends(_auth("audit:read")), s: Session = Depends(session)
) -> dict[str, Any]:
    rows = s.scalars(
        select(Audit)
        .where(Audit.tenant_id == tenant_id)
        .order_by(Audit.created_at.desc())
        .limit(500)
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "actor": row.actor,
                "action": row.action,
                "target": row.target,
                "correlation_id": row.correlation_id,
                "before": row.before,
                "after": row.after,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    }


@router.get("/admin/health")
def admin_health(
    tenant_id: str = Depends(_auth("admin")), s: Session = Depends(session)
) -> dict[str, Any]:
    return {
        "status": "ok",
        "tenant_id": tenant_id,
        "pending_provider_events": len(
            s.scalars(
                select(SmsProviderEventInbox)
                .where(SmsProviderEventInbox.state == "pending")
                .limit(1000)
            ).all()
        ),
    }
