from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from billing.engine import segment_info

E164_PATTERN = re.compile(r"^\+[1-9][0-9]{5,18}$")
SENDER_PATTERN = re.compile(r"^(?:\+[1-9][0-9]{5,18}|[A-Za-z0-9]{1,20})$")


class SmsCommandPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: uuid.UUID
    channel: Literal["sms"]
    destination: str = Field(min_length=7, max_length=20)
    sender: str = Field(min_length=1, max_length=20)
    content: str = Field(min_length=1, max_length=5000)
    encoding: Literal["GSM-7", "UCS-2"]
    characters: int = Field(ge=1)
    segments: int = Field(ge=1)
    category: Literal["transactional", "service", "marketing"]
    client_reference: str = Field(min_length=1, max_length=120)
    scheduled_at: datetime | None
    billing_account_id: str | None = Field(default=None, min_length=1, max_length=128)
    campaign_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, value: str) -> str:
        if not E164_PATTERN.fullmatch(value):
            raise ValueError("destination must be an E.164 number")
        return value

    @field_validator("sender")
    @classmethod
    def validate_sender(cls, value: str) -> str:
        if not SENDER_PATTERN.fullmatch(value):
            raise ValueError(
                "sender must be an E.164 number or 1-20 alphanumeric characters"
            )
        return value

    @field_validator("scheduled_at")
    @classmethod
    def require_scheduled_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_at must include timezone")
        return value

    @model_validator(mode="after")
    def validate_segment_evidence(self) -> "SmsCommandPayload":
        encoding, characters, segments = segment_info(self.content)
        if self.encoding != encoding:
            raise ValueError("encoding does not match content")
        if self.characters != characters:
            raise ValueError("characters does not match content")
        if self.segments != segments:
            raise ValueError("segments does not match content")
        return self


class SmsCommandEnvelope(BaseModel):
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
    payload: SmsCommandPayload


class DlrEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=180)
    operation_id: uuid.UUID | None = None
    tenant_id: str | None = Field(default=None, max_length=128)
    provider_message_id: str = Field(min_length=1, max_length=180)
    message_status: str = Field(min_length=1, max_length=80)
    connector: str | None = Field(default=None, max_length=120)
    id_smsc: str | None = Field(default=None, max_length=180)
    level: str | None = Field(default=None, max_length=8)
    error_code: str | None = Field(default=None, max_length=80)
    occurred_at: datetime | None = None


class InboundEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    event_id: str = Field(min_length=1, max_length=180)
    tenant_id: str | None = Field(default=None, max_length=128)
    provider_message_id: str = Field(min_length=1, max_length=180)
    sender: str = Field(alias="from", min_length=1, max_length=32)
    destination: str = Field(alias="to", min_length=1, max_length=32)
    content: str = Field(min_length=1, max_length=5000)
    connector: str | None = Field(default=None, max_length=120)
    occurred_at: datetime | None = None

    @field_validator("sender", "destination")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        if not E164_PATTERN.fullmatch(value):
            raise ValueError("inbound phone values must be E.164 numbers")
        return value


class ReconciliationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(
        default="unknown_provider_outcome", min_length=1, max_length=180
    )


class DispatchCallbacksRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=25, ge=1, le=100)


class ProviderMessageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: uuid.UUID
    message_id: uuid.UUID
    tenant_id: str
    state: str
    provider_status: str | None
    provider_message_id: str | None
    submission_attempts: int
    reconciliation_attempts: int
    encoding: str
    characters: int
    segments: int
    reservation_id: str | None
    idempotent_replay: bool = False


class ProviderHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    provider: str
    connector: str | None
    live_submission_enabled: bool
    callback_delivery_enabled: bool
    pending_operations: int
    reconciliation_required: int
    pending_callbacks: int
