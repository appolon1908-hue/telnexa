import ssl
from unittest.mock import Mock, patch

import pytest

from billing.config import settings
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
