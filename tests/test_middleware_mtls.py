import hashlib
import hmac
import json
import ssl
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs

import pytest

from billing.config import settings
from billing import worker
from billing.worker import middleware_ssl_context


def test_middleware_endpoint_requires_canonical_https_hostname(tmp_path, monkeypatch):
    ca = tmp_path / "ca.crt"
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    for item in (ca, cert, key):
        item.write_text("test-only")
    monkeypatch.setattr(settings, "middleware_ca_file", str(ca))
    monkeypatch.setattr(settings, "middleware_client_cert_file", str(cert))
    monkeypatch.setattr(settings, "middleware_client_key_file", str(key))

    for invalid in (
        "http://middleware.internal.codestra.agency/api/v1/events/telnexa",
        "https://10.40.0.1/api/v1/events/telnexa",
        "https://middleware.internal.codestra.agency:444/api/v1/events/telnexa",
        "https://middleware.internal.codestra.agency/other",
        "https://middleware.internal.codestra.agency/api/v1/events/telnexa?token=x",
        "https://user@middleware.internal.codestra.agency/api/v1/events/telnexa",
    ):
        monkeypatch.setattr(settings, "middleware_url", invalid)
        with pytest.raises(RuntimeError, match="canonical_middleware_endpoint_required"):
            middleware_ssl_context()


def test_middleware_context_requires_and_loads_client_identity(tmp_path, monkeypatch):
    monkeypatch.setattr(
        settings,
        "middleware_url",
        "https://middleware.internal.codestra.agency/api/v1/events/telnexa",
    )
    missing = tmp_path / "missing"
    monkeypatch.setattr(settings, "middleware_ca_file", str(missing))
    monkeypatch.setattr(settings, "middleware_client_cert_file", str(missing))
    monkeypatch.setattr(settings, "middleware_client_key_file", str(missing))
    with pytest.raises(RuntimeError, match="middleware_mtls_identity_unavailable"):
        middleware_ssl_context()

    ca = tmp_path / "ca.crt"
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    for item in (ca, cert, key):
        item.write_text("test-only")
    monkeypatch.setattr(settings, "middleware_ca_file", str(ca))
    monkeypatch.setattr(settings, "middleware_client_cert_file", str(cert))
    monkeypatch.setattr(settings, "middleware_client_key_file", str(key))
    context = Mock()
    with patch("billing.worker.ssl.create_default_context", return_value=context) as create:
        assert middleware_ssl_context() is context
    create.assert_called_once_with(cafile=str(ca))
    context.load_cert_chain.assert_called_once_with(certfile=str(cert), keyfile=str(key))
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


class TokenResponse:
    def __init__(self, value):
        self.value = json.dumps(value).encode()

    def read(self, maximum):
        return self.value[:maximum]


def test_middleware_token_uses_bounded_client_credentials(tmp_path, monkeypatch):
    secret = tmp_path / "client-secret"
    secret.write_text("client-secret-" + ("x" * 32))
    monkeypatch.setattr(settings, "middleware_client_secret_file", str(secret))
    monkeypatch.setattr(
        settings,
        "middleware_token_url",
        "https://auth.codestra.co/realms/codestra/protocol/openid-connect/token",
    )
    monkeypatch.setattr(settings, "middleware_client_id", "telnexa-gateway")
    context = Mock()
    seen = []

    def token_sender(request, **kwargs):
        seen.append(request)
        return TokenResponse({"access_token": "a" * 64, "token_type": "Bearer", "expires_in": 300})

    with patch("billing.worker.ssl.create_default_context", return_value=context):
        token, expires_at = worker.fetch_middleware_access_token(
            sender=token_sender, monotonic=lambda: 100.0
        )
    assert token == "a" * 64 and expires_at == 370.0
    assert len(seen) == 1
    request_body = parse_qs(seen[0].data.decode("ascii"), strict_parsing=True)
    assert request_body == {
        "grant_type": ["client_credentials"],
        "client_id": ["telnexa-gateway"],
        "client_secret": [secret.read_text()],
    }
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


@pytest.mark.parametrize(
    "invalid",
    [
        "http://auth.codestra.co/realms/codestra/protocol/openid-connect/token",
        "https://other.invalid/realms/codestra/protocol/openid-connect/token",
        "https://auth.codestra.co/realms/codestra/protocol/openid-connect/token?secret=x",
        "https://user@auth.codestra.co/realms/codestra/protocol/openid-connect/token",
    ],
)
def test_middleware_token_authority_is_fixed(invalid, monkeypatch):
    monkeypatch.setattr(settings, "middleware_token_url", invalid)
    with pytest.raises(RuntimeError, match="canonical_middleware_identity_required"):
        worker.middleware_identity_ssl_context()


def test_canonical_middleware_event_headers_and_signature(tmp_path, monkeypatch):
    secret = tmp_path / "event-hmac"
    secret.write_text("event-hmac-" + ("y" * 32))
    monkeypatch.setattr(settings, "middleware_hmac_secret_file", str(secret))
    monkeypatch.setattr(worker.time, "time", lambda: 1_800_000_000)
    event_id = "11111111-1111-4111-8111-111111111111"
    envelope = {
        "event_id": event_id,
        "event_type": "codestra.sms.message.delivered",
        "event_version": "1.0",
        "occurred_at": "2026-09-12T12:00:00+00:00",
        "received_at": "2026-09-12T12:00:01+00:00",
        "source": "telnexa-gateway",
        "tenant_id": "tenant-1",
        "correlation_id": "corr-1",
        "causation_id": "provider-event-1",
        "idempotency_key": event_id,
        "payload": {"message_id": "message-1", "status": "delivered"},
        "metadata": {"provider": "jasmin"},
    }
    row = SimpleNamespace(
        id=event_id,
        tenant_id="tenant-1",
        correlation_id="corr-1",
        envelope=envelope,
    )
    request = worker.middleware_event_request(row, "z" * 64)
    headers = {key.lower(): value for key, value in request.header_items()}
    body_sha = hashlib.sha256(request.data).hexdigest()
    canonical = "\n".join(
        (
            "v1",
            "POST",
            "/api/v1/events/telnexa",
            "1800000000",
            event_id,
            "telnexa-gateway",
            body_sha,
        )
    ).encode()
    expected = hmac.new(secret.read_bytes(), canonical, hashlib.sha256).hexdigest()
    assert request.full_url == ("https://middleware.internal.codestra.agency/api/v1/events/telnexa")
    assert headers["authorization"] == "Bearer " + ("z" * 64)
    assert headers["idempotency-key"] == event_id
    assert headers["x-codestra-event-id"] == event_id
    assert headers["x-codestra-event-type"] == envelope["event_type"]
    assert headers["x-codestra-source"] == "telnexa-gateway"
    assert headers["x-codestra-tenant-id"] == "tenant-1"
    assert headers["x-correlation-id"] == "corr-1"
    assert headers["x-codestra-signature"] == "sha256=" + expected
