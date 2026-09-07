from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol


class SubmissionOutcome(str, Enum):
    ACCEPTED = "ACCEPTED"
    DEFINITIVE_REJECT = "DEFINITIVE_REJECT"
    SAFE_RETRY = "SAFE_RETRY"
    AMBIGUOUS = "AMBIGUOUS"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class NormalizedSubmission:
    message_id: str
    tenant_id: str
    correlation_id: str
    destination: str
    sender: str
    content: str
    encoding: str
    segments: int
    callback_reference: str
    dlr_level: int = 3


@dataclass(frozen=True)
class SubmissionResult:
    outcome: SubmissionOutcome
    provider_message_id: str | None = None
    provider_code: str | None = None
    retryable: bool = False
    ambiguous: bool = False
    accepted_at: datetime | None = None
    http_status: int | None = None
    safe_metadata: dict = field(default_factory=dict)


class SmsProviderAdapter(Protocol):
    def submit(self, submission: NormalizedSubmission) -> SubmissionResult: ...
    def health(self) -> dict: ...
    def normalize_dlr(self, event: dict) -> dict: ...
    def normalize_mo(self, event: dict) -> dict: ...
