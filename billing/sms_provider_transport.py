"""Fail-closed Jasmin transports, read-back, and callback signing helpers."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urlparse

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .sms_provider_contracts import (
    CallbackAuthenticationError,
    EVENT_ID,
    ProviderDisabled,
    SmsCommand,
    canonical_json,
    sha256,
)
from .sms_provider_models import SmsBase, SmsOperation


@dataclass(frozen=True, slots=True)
class SubmissionResult:
    outcome: Literal["accepted", "rejected", "unknown"]
    provider_reference: str | None = None
    provider_status: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ReadbackResult:
    outcome: Literal["accepted", "delivered", "failed", "unknown"]
    provider_reference: str | None = None
    provider_status: str | None = None
    error: str | None = None


class JasminTransport(Protocol):
    name: str

    def submit(self, command: SmsCommand) -> SubmissionResult: ...

    def readback(self, operation: SmsOperation) -> ReadbackResult: ...

    def health(self) -> dict[str, Any]: ...


class DisabledJasminTransport:
    name = "disabled"

    def submit(self, command: SmsCommand) -> SubmissionResult:
        raise ProviderDisabled("Jasmin submission is disabled")

    def readback(self, operation: SmsOperation) -> ReadbackResult:
        return ReadbackResult("unknown", error="Jasmin read-back is disabled")

    def health(self) -> dict[str, Any]:
        return {"status": "disabled", "liveSubmission": False}


class ScriptedJasminTransport:
    """Deterministic no-effect transport used by unit tests."""

    name = "scripted-no-effect"

    def __init__(
        self,
        *,
        submissions: list[SubmissionResult] | None = None,
        readbacks: list[ReadbackResult] | None = None,
    ) -> None:
        self.submissions = list(submissions or [SubmissionResult("accepted", "sim-ref")])
        self.readbacks = list(readbacks or [ReadbackResult("accepted", "sim-ref")])
        self.submit_calls = 0
        self.readback_calls = 0

    def submit(self, command: SmsCommand) -> SubmissionResult:
        self.submit_calls += 1
        if not self.submissions:
            return SubmissionResult("unknown", error="script exhausted")
        return self.submissions.pop(0)

    def readback(self, operation: SmsOperation) -> ReadbackResult:
        self.readback_calls += 1
        if not self.readbacks:
            return ReadbackResult("unknown", error="script exhausted")
        return self.readbacks.pop(0)

    def health(self) -> dict[str, Any]:
        return {
            "status": "synthetic",
            "liveSubmission": False,
            "submitCalls": self.submit_calls,
            "readbackCalls": self.readback_calls,
        }


class HttpJasminSimulatorTransport:
    """HTTP transport restricted to the internal, no-effect Jasmin simulator."""

    name = "http-simulator-no-effect"

    def __init__(self, base_url: str, *, timeout_seconds: float = 2.0) -> None:
        if os.environ.get("TELNEXA_JASMIN_SIMULATOR_ENABLED", "false").lower() != "true":
            raise ProviderDisabled("Jasmin simulator transport is not enabled")
        if os.environ.get("JASMIN_LIVE_SUBMISSION", "false").lower() == "true":
            raise ProviderDisabled("simulator transport cannot run with live submission enabled")
        parsed = urlparse(base_url)
        allowed_hosts = {"jasmin-simulator", "127.0.0.1", "localhost"}
        if parsed.scheme != "http" or parsed.hostname not in allowed_hosts:
            raise ProviderDisabled("simulator endpoint must be an approved internal HTTP host")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def submit(self, command: SmsCommand) -> SubmissionResult:
        payload = command.payload.model_dump(mode="json")
        try:
            response = httpx.post(
                self.base_url + "/submit",
                json=payload,
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            return SubmissionResult("unknown", error=type(error).__name__)
        if response.status_code == 202:
            body = response.json()
            return SubmissionResult(
                "accepted",
                provider_reference=str(body["provider_reference"]),
                provider_status=str(body.get("status") or "accepted"),
            )
        if 400 <= response.status_code < 500:
            return SubmissionResult(
                "rejected",
                provider_status=str(response.status_code),
                error=response.text[:500],
            )
        return SubmissionResult(
            "unknown",
            provider_status=str(response.status_code),
            error=response.text[:500],
        )

    def readback(self, operation: SmsOperation) -> ReadbackResult:
        try:
            response = httpx.get(
                self.base_url + "/messages/" + operation.client_reference,
                timeout=self.timeout_seconds,
            )
        except (httpx.TimeoutException, httpx.TransportError) as error:
            return ReadbackResult("unknown", error=type(error).__name__)
        if response.status_code == 404:
            return ReadbackResult("unknown", error="not_found")
        if response.status_code >= 500:
            return ReadbackResult(
                "unknown",
                provider_status=str(response.status_code),
                error=response.text[:500],
            )
        response.raise_for_status()
        body = response.json()
        provider_status = str(body.get("status") or "unknown").lower()
        if provider_status in {"delivered", "delivrd"}:
            outcome: Literal["accepted", "delivered", "failed", "unknown"] = "delivered"
        elif provider_status in {"accepted", "submitted", "sent", "enroute"}:
            outcome = "accepted"
        elif provider_status in {"failed", "rejected", "expired", "undeliverable"}:
            outcome = "failed"
        else:
            outcome = "unknown"
        return ReadbackResult(
            outcome,
            provider_reference=str(body.get("provider_reference") or "") or None,
            provider_status=provider_status,
        )

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(self.base_url + "/health", timeout=self.timeout_seconds)
            return {
                "status": "synthetic" if response.status_code == 200 else "degraded",
                "liveSubmission": False,
                "upstreamStatus": response.status_code,
            }
        except httpx.HTTPError:
            return {"status": "unavailable", "liveSubmission": False}


def create_session_factory(
    database_url: str,
    *,
    create_schema: bool = True,
) -> tuple[Any, sessionmaker[Session]]:
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    engine = create_engine(database_url, pool_pre_ping=True, connect_args=connect_args)
    if create_schema:
        SmsBase.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def _canonical_provider_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized in {"accepted", "acceptd", "submitted", "submit_sm_resp"}:
        return "provider_accepted"
    if normalized in {"sent", "enroute"}:
        return "sent"
    if normalized in {"delivered", "delivrd"}:
        return "delivered"
    if normalized in {"expired"}:
        return "expired"
    if normalized in {"cancelled", "canceled"}:
        return "cancelled"
    if normalized in {"failed", "rejected", "undeliverable", "undeliv"}:
        return "failed"
    return "reconciliation_required"


def verify_callback(
    *,
    secret: str,
    timestamp: str,
    event_id: str,
    raw_body: bytes,
    signature: str,
    now: int | None = None,
    maximum_clock_skew_seconds: int = 300,
) -> str:
    if len(secret.encode("utf-8")) < 32:
        raise CallbackAuthenticationError("provider callback secret does not satisfy policy")
    if EVENT_ID.fullmatch(event_id) is None:
        raise CallbackAuthenticationError("provider callback event ID is invalid")
    try:
        numeric_timestamp = int(timestamp)
    except ValueError as error:
        raise CallbackAuthenticationError("provider callback timestamp is invalid") from error
    if abs((now or int(time.time())) - numeric_timestamp) > maximum_clock_skew_seconds:
        raise CallbackAuthenticationError("provider callback timestamp expired")
    candidate = signature.removeprefix("sha256=").removeprefix("v1=").lower()
    expected = hmac.new(
        secret.encode("utf-8"),
        timestamp.encode("ascii") + b"." + raw_body,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(candidate, expected):
        raise CallbackAuthenticationError("provider callback signature is invalid")
    return sha256(raw_body)


def middleware_callback(
    *,
    secret: str,
    event_id: str,
    payload: dict[str, Any],
    timestamp: int | None = None,
) -> tuple[bytes, dict[str, str]]:
    if len(secret.encode("utf-8")) < 32:
        raise ValueError("Middleware webhook secret must contain at least 32 bytes")
    raw = canonical_json(payload)
    ts = str(timestamp or int(time.time()))
    signature = hmac.new(
        secret.encode("utf-8"),
        ts.encode("ascii") + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    return raw, {
        "Content-Type": "application/json",
        "X-Codestra-Timestamp": ts,
        "X-Codestra-Event-Id": event_id,
        "X-Codestra-Signature": "v1=" + signature,
    }


__all__ = [
    "SubmissionResult",
    "ReadbackResult",
    "JasminTransport",
    "DisabledJasminTransport",
    "ScriptedJasminTransport",
    "HttpJasminSimulatorTransport",
    "create_session_factory",
    "verify_callback",
    "middleware_callback",
]
