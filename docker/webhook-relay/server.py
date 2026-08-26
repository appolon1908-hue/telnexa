#!/usr/bin/env python3
"""Normalize Jasmin callbacks and forward canonical Codestra webhook envelopes."""

import hashlib
import hmac
import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CANONICAL_ISSUER = "https://auth.codestra.co/realms/codestra"
CANONICAL_TOKEN_ENDPOINT = CANONICAL_ISSUER + "/protocol/openid-connect/token"
CLIENT_ID = "telnexa-gateway"
TARGET_PATH = "/api/v1/telnexa/events"
TARGET = os.environ.get("WEBHOOK_TARGET_BASE_URL", "").rstrip("/")
TIMEOUT = float(os.environ.get("WEBHOOK_TIMEOUT_SECONDS", "10"))
ALLOWED = {"inbound", "dlr", "failed"}
_token_cache = {}


def _read_private_file(path_value, label):
    path = Path(path_value)
    if not path_value or not path.is_file() or path.is_symlink():
        raise RuntimeError(f"{label}_file_unavailable")
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise RuntimeError(f"{label}_file_empty")
    return value


def webhook_secret():
    path = os.environ.get("WEBHOOK_HMAC_SECRET_FILE", "")
    if path:
        return _read_private_file(path, "webhook_hmac_secret").encode()
    if os.environ.get("TELNEXA_ENV", "development").lower() == "production":
        raise RuntimeError("webhook_hmac_secret_file_required")
    value = os.environ.get("WEBHOOK_HMAC_SECRET", "")
    if not value:
        raise RuntimeError("webhook_hmac_secret_unavailable")
    return value.encode()


def _token_endpoint():
    endpoint = os.environ.get("KEYCLOAK_TOKEN_ENDPOINT", CANONICAL_TOKEN_ENDPOINT)
    if endpoint != CANONICAL_TOKEN_ENDPOINT:
        raise RuntimeError("canonical_keycloak_token_endpoint_required")
    configured_client = os.environ.get("TELNEXA_GATEWAY_CLIENT_ID", CLIENT_ID)
    if configured_client != CLIENT_ID:
        raise RuntimeError("canonical_telnexa_client_id_required")
    return endpoint


def access_token(required_scope):
    now = time.monotonic()
    cached = _token_cache.get(required_scope)
    if cached and cached["expires_at"] > now + 15:
        return cached["access_token"]
    secret = _read_private_file(
        os.environ.get("TELNEXA_GATEWAY_CLIENT_SECRET_FILE", ""),
        "telnexa_gateway_client_secret",
    )
    form = urllib.parse.urlencode(
        {
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": secret,
            "scope": required_scope,
        }
    ).encode()
    request = urllib.request.Request(
        _token_endpoint(),
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        document = json.load(response)
    token = document.get("access_token")
    expires_in = document.get("expires_in")
    if (
        not isinstance(token, str)
        or not token
        or not isinstance(expires_in, int)
        or not 1 <= expires_in <= 300
        or document.get("refresh_token")
    ):
        raise RuntimeError("invalid_client_credentials_token_response")
    _token_cache[required_scope] = {
        "access_token": token,
        "expires_at": now + expires_in,
    }
    return token


def make_signature(secret, method, path, timestamp, event_id, payload):
    normalized_path = "/" + "/".join(part for part in path.split("/") if part)
    body_hash = hashlib.sha256(payload).hexdigest()
    canonical = "\n".join(
        (
            "v1",
            method.upper(),
            normalized_path,
            timestamp,
            event_id,
            CLIENT_ID,
            body_hash,
        )
    ).encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def authenticated_source(headers, values):
    path = os.environ.get("TELNEXA_PROVIDER_KEYS_FILE", "")
    try:
        records = json.loads(Path(path).read_text(encoding="utf-8")).get("keys", [])
    except (OSError, ValueError):
        return None
    key_id = headers.get("X-Key-ID", "") or values.pop("source_key_id", "")
    token = headers.get("X-Telnexa-Source-Token", "") or values.pop("source_token", "")
    digest = hashlib.sha256(token.encode()).hexdigest()
    matches = [
        row
        for row in records
        if row.get("id") == key_id
        and row.get("enabled") is True
        and isinstance(row.get("sha256"), str)
        and hmac.compare_digest(row["sha256"], digest)
        and isinstance(row.get("tenant_id"), str)
        and row["tenant_id"]
    ]
    return matches[0] if token and len(matches) == 1 else None


def canonical_event_type(event):
    return {
        "inbound": "codestra.sms.inbound.received",
        "dlr": "codestra.sms.message.delivered",
        "failed": "codestra.sms.message.failed",
    }[event]


def stable_event_id(event, values):
    provider_reference = str(
        values.get("event_id")
        or values.get("id_smsc")
        or values.get("message_id")
        or values.get("id")
        or ""
    )
    material = json.dumps(values, separators=(",", ":"), sort_keys=True)
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"urn:codestra:telnexa:{event}:{provider_reference}:{hashlib.sha256(material.encode()).hexdigest()}",
        )
    )


def build_envelope(event, event_id, principal, values, received_at=None):
    event_type = canonical_event_type(event)
    subject = str(values.get("message_id") or values.get("id_smsc") or values.get("id") or event_id)
    correlation_id = str(values.get("correlation_id") or subject or event_id)
    occurred_at = received_at or datetime.now(timezone.utc).isoformat()
    return {
        "specversion": "1.0",
        "id": event_id,
        "type": event_type,
        "source": "urn:codestra:telnexa-gateway",
        "subject": subject,
        "time": occurred_at,
        "tenant_id": principal["tenant_id"],
        "correlation_id": correlation_id,
        "causation_id": str(values.get("causation_id") or correlation_id),
        "idempotency_key": event_id,
        "schema_version": 1,
        "actor": {"type": "service", "id": CLIENT_ID},
        "delivery_attempt": 1,
        "data": {"provider_event": event, **values},
    }


def tls_context():
    ca_file = os.environ.get("TELNEXA_MIDDLEWARE_CA_FILE", "")
    cert_file = os.environ.get("TELNEXA_MIDDLEWARE_CLIENT_CERT_FILE", "")
    key_file = os.environ.get("TELNEXA_MIDDLEWARE_CLIENT_KEY_FILE", "")
    production = os.environ.get("TELNEXA_ENV", "development").lower() == "production"
    if production and not (ca_file and cert_file and key_file):
        raise RuntimeError("middleware_mtls_material_required")
    context = ssl.create_default_context(cafile=ca_file or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if cert_file or key_file:
        if not (cert_file and key_file):
            raise RuntimeError("middleware_client_certificate_incomplete")
        context.load_cert_chain(certfile=cert_file, keyfile=key_file)
    return context


class Handler(BaseHTTPRequestHandler):
    server_version = "TelnexaWebhookRelay/2"

    def log_message(self, fmt, *args):
        print(
            f"{self.command} {urllib.parse.urlsplit(self.path).path} {args[1] if len(args) > 1 else '-'}",
            flush=True,
        )

    def send(self, status, body):
        data = json.dumps(body, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path)
        if path.path == "/healthz":
            return self.send(200, {"status": "ok"})
        return self.forward(path, dict(urllib.parse.parse_qsl(path.query, keep_blank_values=True)))

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path)
        length = min(int(self.headers.get("Content-Length", "0")), 1048576)
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        try:
            values = (
                json.loads(raw)
                if "application/json" in content_type
                else dict(urllib.parse.parse_qsl(raw.decode(), keep_blank_values=True))
            )
        except (ValueError, UnicodeDecodeError):
            return self.send(400, {"error": "invalid payload"})
        if not isinstance(values, dict):
            return self.send(400, {"error": "invalid payload"})
        return self.forward(path, values)

    def forward(self, path, values):
        parts = path.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "events" or parts[1] not in ALLOWED:
            return self.send(404, {"error": "not found"})
        principal = authenticated_source(self.headers, values)
        if not principal:
            return self.send(401, {"error": "provider source identity required"})
        if not TARGET or not TARGET.lower().startswith("https://"):
            return self.send(503, {"error": "secure middleware target is not configured"})

        event = parts[1]
        event_id = stable_event_id(event, values)
        envelope = build_envelope(event, event_id, principal, values)
        payload = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode()
        timestamp = str(int(time.time()))
        event_type = envelope["type"]
        required_scope = (
            "sms.inbound.publish"
            if event_type == "codestra.sms.inbound.received"
            else "sms.events.publish"
        )
        try:
            token = access_token(required_scope)
            signature = make_signature(
                webhook_secret(), "POST", TARGET_PATH, timestamp, event_id, payload
            )
            request = urllib.request.Request(
                TARGET + TARGET_PATH,
                data=payload,
                method="POST",
                headers={
                    "Authorization": "Bearer " + token,
                    "Content-Type": "application/json",
                    "Idempotency-Key": event_id,
                    "X-Codestra-Event-Id": event_id,
                    "X-Codestra-Event-Type": event_type,
                    "X-Codestra-Source": CLIENT_ID,
                    "X-Codestra-Tenant-Id": principal["tenant_id"],
                    "X-Codestra-Timestamp": timestamp,
                    "X-Codestra-Signature": f"sha256={signature}",
                    "X-Correlation-Id": envelope["correlation_id"],
                },
            )
            with urllib.request.urlopen(
                request, timeout=TIMEOUT, context=tls_context()
            ) as response:
                accepted = 200 <= response.status < 300
                return self.send(202 if accepted else 502, {"accepted": accepted})
        except (OSError, RuntimeError, urllib.error.URLError, TimeoutError):
            return self.send(502, {"error": "middleware delivery failed"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
