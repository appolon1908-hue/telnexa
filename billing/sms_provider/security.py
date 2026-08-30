from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}


class ProviderConfigurationError(RuntimeError):
    """Provider configuration is incomplete or unsafe."""


class PayloadCipher:
    def __init__(self, key: str | bytes):
        encoded = key.encode() if isinstance(key, str) else key
        try:
            self._fernet = Fernet(encoded)
        except (TypeError, ValueError) as exc:
            raise ProviderConfigurationError(
                "invalid SMS payload encryption key"
            ) from exc

    @classmethod
    def from_env(cls) -> "PayloadCipher":
        path = os.environ.get("TELNEXA_SMS_PAYLOAD_KEY_FILE", "")
        key = _read_secret(path) or os.environ.get("TELNEXA_SMS_PAYLOAD_KEY", "")
        if not key:
            raise ProviderConfigurationError(
                "SMS payload encryption key is not configured"
            )
        return cls(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise ProviderConfigurationError("SMS payload cannot be decrypted") from exc


def canonical_json(payload: dict) -> bytes:
    return json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def make_signature(
    secret: bytes,
    method: str,
    path: str,
    timestamp: str,
    event_id: str,
    payload: bytes,
    *,
    source: str = "telnexa",
) -> str:
    normalized_path = "/" + "/".join(part for part in path.split("/") if part)
    body_hash = hashlib.sha256(payload).hexdigest()
    canonical = "\n".join(
        (
            "v1",
            method.upper(),
            normalized_path,
            timestamp,
            event_id,
            source,
            body_hash,
        )
    ).encode()
    return hmac.new(secret, canonical, hashlib.sha256).hexdigest()


def verify_signature(
    secret: bytes,
    method: str,
    path: str,
    timestamp: str,
    event_id: str,
    payload: bytes,
    signature: str,
    *,
    max_age_seconds: int = 300,
    source: str = "telnexa",
    now_epoch: int | None = None,
) -> bool:
    try:
        timestamp_value = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = int(time.time()) if now_epoch is None else now_epoch
    if abs(current - timestamp_value) > max_age_seconds:
        return False
    presented = signature.removeprefix("sha256=")
    expected = make_signature(
        secret,
        method,
        path,
        timestamp,
        event_id,
        payload,
        source=source,
    )
    return hmac.compare_digest(presented, expected)


def provider_effects_enabled() -> bool:
    gates = (
        "ENABLE_EXTERNAL_DELIVERY",
        "SMS_DELIVERY",
        "SMS_DELIVERY_ENABLED",
        "LIVE_SMS_DELIVERY",
        "TELNEXA_PROVIDER_EXECUTION_ENABLED",
        "JASMIN_LIVE_SUBMISSION",
    )
    return all(_truthy(name) for name in gates)


def callback_delivery_enabled() -> bool:
    gates = (
        "ENABLE_EXTERNAL_DELIVERY",
        "LIVE_SMS_DELIVERY",
        "TELNEXA_CALLBACK_DELIVERY_ENABLED",
    )
    return all(_truthy(name) for name in gates)


def read_secret(path: str) -> str:
    return _read_secret(path)


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def _read_secret(path: str) -> str:
    if not path:
        return ""
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
