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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SECRET = os.environ["WEBHOOK_HMAC_SECRET"].encode()
TARGET = os.environ.get("WEBHOOK_TARGET_BASE_URL", "").rstrip("/")
TIMEOUT = float(os.environ.get("WEBHOOK_TIMEOUT_SECONDS", "10"))
ALLOWED = {"inbound", "dlr", "failed"}


def make_signature(secret, timestamp, payload):
    return hmac.new(secret, timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()


class Handler(BaseHTTPRequestHandler):
    server_version = "TelnexaWebhookRelay/1"

    def log_message(self, fmt, *args):
        # Log method/path/status only; never callback query strings or payloads.
        print(f'{self.command} {urllib.parse.urlsplit(self.path).path} {args[1] if len(args) > 1 else "-"}', flush=True)

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
            values = json.loads(raw) if "application/json" in content_type else dict(urllib.parse.parse_qsl(raw.decode(), keep_blank_values=True))
        except (ValueError, UnicodeDecodeError):
            return self.send(400, {"error": "invalid payload"})
        return self.forward(path, values)

    def forward(self, path, values):
        parts = path.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "events" or parts[1] not in ALLOWED:
            return self.send(404, {"error": "not found"})
        event = parts[1]
        payload = json.dumps({"event": event, "received_at": int(time.time()), "data": values}, separators=(",", ":"), sort_keys=True).encode()
        if not TARGET:
            return self.send(503, {"error": "middleware target is not configured"})
        timestamp = str(int(time.time()))
        signature = make_signature(SECRET, timestamp, payload)
        request = urllib.request.Request(
            f"{TARGET}/webhooks/sms/{event}", data=payload, method="POST",
            headers={"Content-Type": "application/json", "X-Telnexa-Timestamp": timestamp, "X-Telnexa-Signature": f"sha256={signature}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return self.send(202 if response.status < 300 else 502, {"accepted": response.status < 300})
        except (urllib.error.URLError, TimeoutError):
            return self.send(502, {"error": "middleware delivery failed"})


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
