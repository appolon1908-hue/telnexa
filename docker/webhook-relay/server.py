#!/usr/bin/env python3
"""Authenticate Jasmin callbacks and forward them to Telnexa's durable inbox."""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    SECRET = Path(os.environ["TELNEXA_PROVIDER_EVENT_HMAC_SECRET_FILE"]).read_bytes().strip()
except (KeyError, OSError):
    SECRET = (
        b""
        if os.environ.get("TELNEXA_PROVIDER_EVENT_HMAC_SECRET_FILE")
        else os.environ.get("WEBHOOK_HMAC_SECRET", "").encode()
    )
TARGET = os.environ.get("TELNEXA_PROVIDER_EVENT_URL", "").rstrip("/")
TIMEOUT = float(os.environ.get("WEBHOOK_TIMEOUT_SECONDS", "10"))
ALLOWED = {"inbound", "dlr", "failed"}


def make_signature(secret, method, path, timestamp, event_id, payload, source_key_id=None):
    normalized_path = "/" + "/".join(part for part in path.split("/") if part)
    body_hash = hashlib.sha256(payload).hexdigest()
    canonical = "\n".join(
        (
            "v2" if source_key_id is not None else "v1",
            method.upper(),
            normalized_path,
            timestamp,
            event_id,
            "telnexa",
            *([source_key_id] if source_key_id is not None else []),
            body_hash,
        )
    ).encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def authenticated_source(headers, values):
    path = os.environ.get("TELNEXA_PROVIDER_KEYS_FILE", "")
    if not isinstance(values, dict):
        return False
    # Strip query/body credentials even if the header takes precedence.
    body_key = values.pop("source_key_id", "")
    body_token = values.pop("source_token", "")
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.loads(handle.read(1048577))
        records = document.get("keys", [])
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            return False
    except (OSError, ValueError, AttributeError, RecursionError):
        return False
    key_id = headers.get("X-Key-ID", "") or body_key
    token = headers.get("X-Telnexa-Source-Token", "") or body_token
    if not isinstance(key_id, str) or not isinstance(token, str) or len(token) > 8192:
        return False
    digest = hashlib.sha256(token.encode()).hexdigest()
    matches = [
        row
        for row in records
        if row.get("id") == key_id
        and row.get("enabled") is True
        and isinstance(row.get("sha256"), str)
        and row["sha256"].isascii()
        and hmac.compare_digest(row["sha256"], digest)
    ]
    return key_id if token and len(matches) == 1 else None


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect denied", headers, fp)


class Handler(BaseHTTPRequestHandler):
    server_version = "TelnexaWebhookRelay/1"

    def log_message(self, fmt, *args):
        # Log method/path/status only; never callback query strings or payloads.
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
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not lengths[0].isdigit():
            return self.send(400, {"error": "invalid content length"})
        if len(lengths[0]) > 12 or int(lengths[0]) > 1048576:
            return self.send(413, {"error": "payload too large"})
        length = int(lengths[0])
        raw = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        try:
            values = (
                json.loads(raw)
                if "application/json" in content_type
                else dict(urllib.parse.parse_qsl(raw.decode(), keep_blank_values=True))
            )
        except (ValueError, UnicodeDecodeError, RecursionError):
            return self.send(400, {"error": "invalid payload"})
        return self.forward(path, values)

    def forward(self, path, values):
        parts = path.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "events" or parts[1] not in ALLOWED:
            return self.send(404, {"error": "not found"})
        if not isinstance(values, dict):
            return self.send(400, {"error": "object payload required"})
        source_key_id = authenticated_source(self.headers, values)
        if not source_key_id:
            return self.send(401, {"error": "provider source identity required"})
        event = parts[1]
        payload = json.dumps(
            {"event": event, "data": values},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        if TARGET != "http://billing-api:8000" or len(SECRET) < 32:
            return self.send(503, {"error": "private inbox identity is not configured"})
        timestamp = str(int(time.time()))
        event_id = hashlib.sha256(payload).hexdigest()
        target_path = "/internal/v1/provider-events/jasmin"
        signature = make_signature(
            SECRET, "POST", target_path, timestamp, event_id, payload, source_key_id
        )
        request = urllib.request.Request(
            f"{TARGET}{target_path}",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Signature-Version": "v2",
                "X-Telnexa-Timestamp": timestamp,
                "X-Telnexa-Event-Id": event_id,
                "X-Telnexa-Signature": f"sha256={signature}",
                # Preserve the authenticated provider identity for composite DLR correlation.
                "X-Key-ID": source_key_id,
            },
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(
                request, timeout=TIMEOUT
            ) as response:
                return self.send(
                    202 if response.status < 300 else 502,
                    {"accepted": response.status < 300},
                )
        except (urllib.error.URLError, TimeoutError):
            return self.send(502, {"error": "telnexa provider-event delivery failed"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
