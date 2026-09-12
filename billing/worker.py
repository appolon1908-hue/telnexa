import hashlib
import hmac
import json
import ssl
import time
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from sqlalchemy import select
from .config import settings
from .db import SessionLocal
from .models import (
    Outbox,
    Webhook,
    WebhookDelivery,
    MessageEvent,
    Message,
    InboundMessage,
)
from .webhooks import decrypt_secret, validate_webhook_url

MIDDLEWARE_HOST = "middleware.internal.codestra.agency"
MIDDLEWARE_PATH = "/api/v1/events/telnexa"
MIDDLEWARE_IDENTITY_HOST = "auth.codestra.co"
MIDDLEWARE_IDENTITY_PATH = "/realms/codestra/protocol/openid-connect/token"
MIDDLEWARE_SOURCE = "telnexa-gateway"
_token_cache: tuple[str, float] | None = None


def _required_secret(path: str, code: str) -> str:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(code) from exc
    if len(value.encode("utf-8")) < 32:
        raise RuntimeError(code)
    return value


def middleware_identity_ssl_context() -> ssl.SSLContext:
    parsed = urlsplit(settings.middleware_token_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != MIDDLEWARE_IDENTITY_HOST
        or parsed.port not in (None, 443)
        or parsed.path != MIDDLEWARE_IDENTITY_PATH
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or settings.middleware_client_id != MIDDLEWARE_SOURCE
    ):
        raise RuntimeError("canonical_middleware_identity_required")
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def fetch_middleware_access_token(sender=None, monotonic=None) -> tuple[str, float]:
    secret = _required_secret(
        settings.middleware_client_secret_file,
        "middleware_client_identity_unavailable",
    )
    body = urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": MIDDLEWARE_SOURCE,
            "client_secret": secret,
        }
    ).encode("ascii")
    request = urllib.request.Request(
        settings.middleware_token_url,
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    try:
        if sender is None:
            response = urllib.request.urlopen(
                request,
                timeout=10,
                context=middleware_identity_ssl_context(),
            )
        else:
            # Keep authority validation in tests where the transport is mocked.
            middleware_identity_ssl_context()
            response = sender(request, timeout=10)
        raw = response.read(65537)
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("middleware_token_request_failed") from exc
    if len(raw) > 65536:
        raise RuntimeError("middleware_token_response_too_large")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise RuntimeError("middleware_token_response_invalid") from exc
    token = payload.get("access_token") if isinstance(payload, dict) else None
    token_type = payload.get("token_type") if isinstance(payload, dict) else None
    expires_in = payload.get("expires_in") if isinstance(payload, dict) else None
    if (
        not isinstance(token, str)
        or not 32 <= len(token) <= 16384
        or not token.isascii()
        or not isinstance(token_type, str)
        or token_type.lower() != "bearer"
        or isinstance(expires_in, bool)
        or not isinstance(expires_in, (int, float))
        or not 1 <= expires_in <= 300
    ):
        raise RuntimeError("middleware_token_response_invalid")
    now = (monotonic or time.monotonic)()
    return token, now + max(1, float(expires_in) - 30)


def middleware_access_token(sender=None, monotonic=None) -> str:
    global _token_cache
    clock = monotonic or time.monotonic
    now = clock()
    if _token_cache is not None and now < _token_cache[1]:
        return _token_cache[0]
    _token_cache = fetch_middleware_access_token(sender=sender, monotonic=clock)
    return _token_cache[0]


def middleware_event_request(row, token: str) -> urllib.request.Request:
    envelope = row.envelope
    if (
        not isinstance(envelope, dict)
        or envelope.get("event_id") != row.id
        or envelope.get("idempotency_key") != row.id
        or envelope.get("source") != MIDDLEWARE_SOURCE
        or envelope.get("tenant_id") != row.tenant_id
        or envelope.get("correlation_id") != row.correlation_id
        or not isinstance(envelope.get("event_type"), str)
        or not envelope["event_type"].startswith("codestra.sms.")
    ):
        raise RuntimeError("middleware_event_contract_invalid")
    body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
    timestamp = str(int(time.time()))
    body_sha = hashlib.sha256(body).hexdigest()
    canonical = "\n".join(
        (
            "v1",
            "POST",
            MIDDLEWARE_PATH,
            timestamp,
            row.id,
            MIDDLEWARE_SOURCE,
            body_sha,
        )
    ).encode()
    secret = _required_secret(
        settings.middleware_hmac_secret_file,
        "middleware_event_identity_unavailable",
    ).encode("utf-8")
    signature = hmac.new(secret, canonical, hashlib.sha256).hexdigest()
    return urllib.request.Request(
        settings.middleware_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
            "Idempotency-Key": row.id,
            "X-Codestra-Event-Id": row.id,
            "X-Codestra-Event-Type": envelope["event_type"],
            "X-Codestra-Source": MIDDLEWARE_SOURCE,
            "X-Codestra-Tenant-Id": row.tenant_id,
            "X-Codestra-Timestamp": timestamp,
            "X-Codestra-Signature": "sha256=" + signature,
            "X-Correlation-Id": row.correlation_id,
        },
    )


def middleware_ssl_context() -> ssl.SSLContext:
    parsed = urlsplit(settings.middleware_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != MIDDLEWARE_HOST
        or parsed.port not in (None, 443)
        or parsed.path != MIDDLEWARE_PATH
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("canonical_middleware_endpoint_required")
    required = (
        settings.middleware_ca_file,
        settings.middleware_client_cert_file,
        settings.middleware_client_key_file,
    )
    if any(not Path(value).is_file() for value in required):
        raise RuntimeError("middleware_mtls_identity_unavailable")
    context = ssl.create_default_context(cafile=settings.middleware_ca_file)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(
        certfile=settings.middleware_client_cert_file,
        keyfile=settings.middleware_client_key_file,
    )
    return context


def deliver_webhooks(db, sender=None):
    now = datetime.now(timezone.utc)
    rows = db.scalars(
        select(WebhookDelivery)
        .where(
            WebhookDelivery.status.in_(["pending", "retrying"]),
            ((WebhookDelivery.next_attempt_at == None) | (WebhookDelivery.next_attempt_at <= now)),
        )
        .order_by(WebhookDelivery.created_at)
        .limit(20)
        .with_for_update(skip_locked=True)
    ).all()
    sent = 0
    for row in rows:
        hook = db.scalar(
            select(Webhook).where(
                Webhook.id == row.webhook_id,
                Webhook.tenant_id == row.tenant_id,
                Webhook.enabled == True,
            )
        )
        event = db.scalar(
            select(MessageEvent).where(
                MessageEvent.id == row.event_id, MessageEvent.tenant_id == row.tenant_id
            )
        )
        inbound = None
        outbox_event = db.scalar(
            select(Outbox).where(Outbox.id == row.event_id, Outbox.tenant_id == row.tenant_id)
        )
        if outbox_event:
            event_type = outbox_event.event_type
            payload = outbox_event.envelope.get("payload", {})
        elif event:
            msg = db.scalar(
                select(Message).where(
                    Message.id == event.message_id, Message.tenant_id == row.tenant_id
                )
            )
            event_type = "sms." + event.status
            payload = {
                "message_id": event.message_id,
                "status": event.status,
                "raw_provider_status": event.provider_response,
                "correlation_id": msg.correlation_id if msg else None,
            }
        else:
            inbound = db.scalar(
                select(InboundMessage).where(
                    InboundMessage.id == row.event_id,
                    InboundMessage.tenant_id == row.tenant_id,
                )
            )
            event_type = "sms.received"
            payload = (
                {
                    "inbound_message_id": inbound.id,
                    "from": inbound.sender,
                    "to": inbound.destination,
                    "content": inbound.content,
                }
                if inbound
                else {}
            )
        try:
            if not hook or (not event and not inbound and not outbox_event):
                raise RuntimeError("webhook_or_event_unavailable")
            validate_webhook_url(hook.url)
            body = json.dumps(
                {
                    "id": row.id,
                    "type": event_type,
                    "tenant_id": row.tenant_id,
                    "created_at": row.created_at.isoformat(),
                    "data": payload,
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode()
            ts = str(int(time.time()))
            secret = decrypt_secret(hook.secret_ciphertext)
            sig = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
            req = urllib.request.Request(
                hook.url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Telnexa-Webhooks/1.0",
                    "X-Telnexa-Timestamp": ts,
                    "X-Telnexa-Signature": "sha256=" + sig,
                    "X-Telnexa-Event-Id": row.id,
                },
            )
            (sender or urllib.request.urlopen)(req, timeout=10)
            row.status = "delivered"
            row.response_code = 200
            sent += 1
        except Exception:
            row.attempts += 1
            row.status = "failed" if row.attempts >= 8 else "retrying"
            row.next_attempt_at = now + timedelta(
                seconds=min(3600, 30 * (2 ** min(row.attempts, 7)))
            )
    return sent


def once(sender=None, token_sender=None):
    with SessionLocal() as db:
        now = datetime.now(timezone.utc)
        rows = db.scalars(
            select(Outbox)
            .where(
                Outbox.state.in_(["pending", "retrying"]),
                ((Outbox.next_attempt_at == None) | (Outbox.next_attempt_at <= now)),
            )
            .order_by(Outbox.created_at)
            .limit(20)
            .with_for_update(skip_locked=True)
        ).all()
        sent = 0
        token = None
        for row in rows:
            try:
                if token is None:
                    token = middleware_access_token(sender=token_sender)
                req = middleware_event_request(row, token)
                if sender is not None:
                    sender(req, timeout=10)
                else:
                    urllib.request.urlopen(req, timeout=10, context=middleware_ssl_context())
                row.state = "delivered"
                sent += 1
            except Exception as exc:
                row.attempts += 1
                row.state = "dead-lettered" if row.attempts >= 4 else "retrying"
                row.last_error = (
                    str(exc) if isinstance(exc, RuntimeError) else "middleware_delivery_failed"
                )[:500]
                row.next_attempt_at = datetime.now(timezone.utc) + timedelta(
                    seconds=[60, 300, 900, 3600][min(row.attempts - 1, 3)]
                )
        sent += deliver_webhooks(db, sender)
        db.commit()
        return sent


if __name__ == "__main__":
    while True:
        once()
        time.sleep(5)
