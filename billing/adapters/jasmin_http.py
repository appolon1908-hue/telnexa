import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx

from .base import SubmissionOutcome, SubmissionResult
from .errors import AdapterConfigurationError

ACCEPTED = re.compile(r"^Success/[A-Za-z0-9._:-]{1,120}$")


class JasminHttpAdapter:
    def __init__(
        self,
        base_url,
        username_file,
        password_file,
        dlr_base_url,
        source_key_id,
        source_token_file,
        connect_timeout_ms=1000,
        request_timeout_ms=5000,
        transport=None,
    ):
        self.base_url = base_url.rstrip("/")
        self.username_file = username_file
        self.password_file = password_file
        self.dlr_base_url = dlr_base_url.rstrip("/")
        self.source_key_id = source_key_id
        self.source_token_file = source_token_file
        self.timeout = httpx.Timeout(request_timeout_ms / 1000, connect=connect_timeout_ms / 1000)
        self.transport = transport
        if not self.base_url.startswith("http://jasmin:") and not self.base_url.startswith(
            "https://jasmin:"
        ):
            raise AdapterConfigurationError("jasmin_endpoint_must_use_private_service_name")

    @staticmethod
    def _secret(path):
        try:
            value = Path(path).read_text().strip()
        except OSError as exc:
            raise AdapterConfigurationError("provider_secret_unavailable") from exc
        if not value:
            raise AdapterConfigurationError("provider_secret_unavailable")
        return value

    def _params(self, s):
        token = self._secret(self.source_token_file)
        callback = (
            f"{self.dlr_base_url}/events/dlr?source_key_id={quote(self.source_key_id)}"
            f"&source_token={quote(token)}&message_id={quote(s.callback_reference)}"
        )
        return {
            "username": self._secret(self.username_file),
            "password": self._secret(self.password_file),
            "to": s.destination,
            "from": s.sender,
            "content": s.content,
            "coding": "8" if s.encoding == "UCS-2" else "0",
            "dlr": "yes",
            "dlr-level": str(s.dlr_level),
            "dlr-url": callback,
            "dlr-method": "POST",
        }

    @staticmethod
    def parse_response(status, body):
        text = body.decode("utf-8", "replace").strip()
        if status == 200 and ACCEPTED.fullmatch(text):
            return SubmissionResult(
                SubmissionOutcome.ACCEPTED,
                text.split("/", 1)[1],
                accepted_at=datetime.now(timezone.utc),
                http_status=status,
            )
        code = text[:120] if text else f"HTTP_{status}"
        if status in {401, 403} or any(
            x in text.lower() for x in ("authentication", "authorization")
        ):
            return SubmissionResult(
                SubmissionOutcome.DEFINITIVE_REJECT, provider_code=code, http_status=status
            )
        if status in {400, 404, 422} or status == 200:
            return SubmissionResult(
                SubmissionOutcome.DEFINITIVE_REJECT, provider_code=code, http_status=status
            )
        return SubmissionResult(
            SubmissionOutcome.SAFE_RETRY, provider_code=code, retryable=True, http_status=status
        )

    def submit(self, submission):
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.post(self.base_url + "/send", data=self._params(submission))
                body = response.content
                if len(body) > 4096:
                    return SubmissionResult(
                        SubmissionOutcome.DEFINITIVE_REJECT, provider_code="response_too_large"
                    )
                return self.parse_response(response.status_code, body)
        except httpx.ConnectError:
            return SubmissionResult(
                SubmissionOutcome.SAFE_RETRY, provider_code="connect_failed", retryable=True
            )
        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError):
            return SubmissionResult(
                SubmissionOutcome.AMBIGUOUS, provider_code="submission_timeout", ambiguous=True
            )

    def health(self):
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.get(self.base_url + "/ping")
            return {"healthy": response.status_code == 200, "status": response.status_code}
        except httpx.HTTPError:
            return {"healthy": False, "status": None}

    def normalize_dlr(self, event):
        raw = str(event.get("message_status") or event.get("status") or "unknown").upper()
        states = {
            "DELIVRD": "delivered",
            "ACCEPTD": "submitted",
            "ENROUTE": "sent",
            "SENT": "sent",
            "UNDELIV": "undeliverable",
            "EXPIRED": "expired",
            "REJECTD": "failed",
            "FAILED": "failed",
        }
        return {"status": states.get(raw, "unknown"), "provider_message_id": event.get("id")}

    def normalize_mo(self, event):
        return {
            "provider_message_id": event.get("id"),
            "sender": event.get("from"),
            "destination": event.get("to"),
            "content": event.get("content", ""),
            "encoding": event.get("coding", "0"),
        }
