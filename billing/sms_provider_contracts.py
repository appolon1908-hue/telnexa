"""Canonical Telnexa SMS provider contracts and validation helpers."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SDK_CONTRACT_SHA = "63c793e88cca5daecfb5c8a688b8674ab288c522"
MIDDLEWARE_SMS_SHA = "d7fcb30dc70ed54bd7e444e4c2ab59f5b5b5d24e"

E164 = re.compile(r"^\+[1-9][0-9]{5,18}$")
SENDER = re.compile(r"^(?:\+[1-9][0-9]{5,18}|[A-Za-z0-9]{1,20})$")
EVENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")

STOP_KEYWORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"})
HELP_KEYWORDS = frozenset({"HELP"})

GSM_BASIC = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)
GSM_EXTENSION = frozenset("^{}\\[~]|€")

TERMINAL_STATES = frozenset({"delivered", "failed", "expired", "cancelled"})
STATE_RANK = {
    "reserved": 0,
    "submitting": 1,
    "reconciliation_required": 2,
    "manual_review": 2,
    "provider_accepted": 3,
    "submitted": 3,
    "sent": 4,
    "delivered": 5,
    "failed": 5,
    "expired": 5,
    "cancelled": 5,
}


def utcnow() -> datetime:
    return datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256(value: bytes | str) -> str:
    raw = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(raw).hexdigest()


def money(value: Decimal | str | int) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"))


@dataclass(frozen=True, slots=True)
class SegmentInfo:
    encoding: Literal["GSM-7", "UCS-2"]
    characters: int
    segments: int
    encoded_units: int


def segment_info(text: str) -> SegmentInfo:
    if not text:
        raise ValueError("SMS content is required")
    if len(text) > 5_000:
        raise ValueError("SMS content exceeds 5000 characters")
    is_gsm = all(character in GSM_BASIC or character in GSM_EXTENSION for character in text)
    if is_gsm:
        units = sum(2 if character in GSM_EXTENSION else 1 for character in text)
        segments = 1 if units <= 160 else math.ceil(units / 153)
        return SegmentInfo("GSM-7", len(text), segments, units)
    units = len(text.encode("utf-16-be")) // 2
    segments = 1 if units <= 70 else math.ceil(units / 67)
    return SegmentInfo("UCS-2", len(text), segments, units)


def compliance_action(text: str) -> Literal["stop", "help"] | None:
    keyword = text.strip().upper()
    if keyword in STOP_KEYWORDS:
        return "stop"
    if keyword in HELP_KEYWORDS:
        return "help"
    return None


class SmsProviderError(RuntimeError):
    code = "sms_provider_error"
    status_code = 400


class ProviderDisabled(SmsProviderError):
    code = "provider_disabled"
    status_code = 503


class IdempotencyConflict(SmsProviderError):
    code = "idempotency_conflict"
    status_code = 409


class ProviderNotFound(SmsProviderError):
    code = "provider_operation_not_found"
    status_code = 404


class CallbackAuthenticationError(SmsProviderError):
    code = "provider_callback_authentication_failed"
    status_code = 401


class CallbackReplayConflict(SmsProviderError):
    code = "provider_callback_replay_conflict"
    status_code = 409


class SmsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: uuid.UUID
    channel: Literal["sms"]
    destination: str
    sender: str
    content: str = Field(min_length=1, max_length=5_000)
    encoding: Literal["GSM-7", "UCS-2"]
    characters: int = Field(ge=1)
    segments: int = Field(ge=1)
    category: Literal["transactional", "service", "marketing"]
    client_reference: str = Field(min_length=1, max_length=120)
    scheduled_at: datetime | None = None
    billing_account_id: str | None = Field(default=None, max_length=128)
    campaign_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def validate_sms_contract(self) -> "SmsPayload":
        if E164.fullmatch(self.destination) is None:
            raise ValueError("destination must be E.164")
        if SENDER.fullmatch(self.sender) is None:
            raise ValueError("sender must be an approved E.164 or alphanumeric identity")
        computed = segment_info(self.content)
        if (
            computed.encoding != self.encoding
            or computed.characters != self.characters
            or computed.segments != self.segments
        ):
            raise ValueError("encoding and segment metadata do not match SMS content")
        return self


class SmsCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: uuid.UUID
    command_type: Literal["sms.message.submit.v1"]
    command_version: Literal["1.0"]
    target: Literal["telnexa-sms"]
    tenant_id: str = Field(min_length=1, max_length=128)
    requested_by: str = Field(min_length=1, max_length=300)
    correlation_id: str = Field(min_length=1, max_length=180)
    idempotency_key: str = Field(min_length=8, max_length=180)
    capability: Literal["SMS_DELIVERY"]
    payload: SmsPayload


class DlrCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128)
    message_id: uuid.UUID | None = None
    provider_reference: str | None = Field(default=None, max_length=160)
    provider_status: str = Field(min_length=1, max_length=80)
    occurred_at: datetime
    failure_code: str | None = Field(default=None, max_length=120)
    failure_message: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def require_message_identity(self) -> "DlrCallback":
        if self.message_id is None and not self.provider_reference:
            raise ValueError("DLR requires message_id or provider_reference")
        return self


class MoCallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128)
    provider_message_id: str = Field(min_length=1, max_length=160)
    sender: str
    destination: str
    content: str = Field(min_length=1, max_length=5_000)
    occurred_at: datetime

    @model_validator(mode="after")
    def validate_addresses(self) -> "MoCallback":
        if E164.fullmatch(self.sender) is None or E164.fullmatch(self.destination) is None:
            raise ValueError("MO sender and destination must be E.164")
        return self



__all__ = [
    "SDK_CONTRACT_SHA", "MIDDLEWARE_SMS_SHA", "STATE_RANK",
    "SmsProviderError", "ProviderDisabled", "IdempotencyConflict",
    "ProviderNotFound", "CallbackAuthenticationError", "CallbackReplayConflict",
    "SmsPayload", "SmsCommand", "DlrCallback", "MoCallback",
    "SegmentInfo", "segment_info", "compliance_action", "canonical_json",
    "sha256", "money", "utcnow", "uid",
]
