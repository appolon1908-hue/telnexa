#!/usr/bin/env python3
"""Normalize Jasmin callbacks and forward them with an HMAC signature."""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ["WEBHOOK_HMAC_SECRET"].encode()
TARGET = os.environ.get("WEBHOOK_TARGET_BASE_URL", "").rstrip("/")
TIMEOUT = float(os.environ.get("WEBHOOK_TIMEOUT_SECONDS", "10"))
ALLOWED = {"inbound", "dlr", "failed"}


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
            "telnexa",
            body_hash,
        )
    ).encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def authenticated_source(headers, values):
    path = os.environ.get("TELNEXA_PROVIDER_KEYS_FILE", "")
    try:
        records = json.loads(open(path, encoding="utf-8").read()).get("keys", [])
    except (OSError, ValueError):
        return False
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
    ]
    return bool(token) and len(matches) == 1


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
        return self.forward(path, values)

    def forward(self, path, values):
        parts = path.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "events" or parts[1] not in ALLOWED:
            return self.send(404, {"error": "not found"})
        if not authenticated_source(self.headers, values):
            return self.send(401, {"error": "provider source identity required"})
        event = parts[1]
        payload = json.dumps(
            {"event": event, "received_at": int(time.time()), "data": values},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        if not TARGET:
            return self.send(503, {"error": "middleware target is not configured"})
        timestamp = str(int(time.time()))
        event_id = str(uuid.uuid4())
        target_path = f"/webhooks/sms/{event}"
        signature = make_signature(SECRET, "POST", target_path, timestamp, event_id, payload)
        request = urllib.request.Request(
            f"{TARGET}/webhooks/sms/{event}",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Signature-Version": "v1",
                "X-Telnexa-Timestamp": timestamp,
                "X-Telnexa-Event-Id": event_id,
                "X-Telnexa-Signature": f"sha256={signature}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return self.send(
                    202 if response.status < 300 else 502,
                    {"accepted": response.status < 300},
                )
        except (urllib.error.URLError, TimeoutError):
            return self.send(502, {"error": "middleware delivery failed"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
