from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_portal_can_reach_only_the_canonical_identity_origin():
    source = (ROOT / "billing/app.py").read_text()
    assert "connect-src 'self' https://auth.codestra.co" in source
    assert "https://auth.codestra.agency" not in source


def test_prometheus_uses_private_metrics_credential():
    compose = (ROOT / "docker-compose.yml").read_text()
    config = (ROOT / "config/prometheus/prometheus.yml").read_text()
    assert "secrets: [telnexa_metrics_token]" in compose
    assert "credentials_file: /run/secrets/telnexa_metrics_token" in config


def test_quick_start_provisions_required_private_contract():
    generator = (ROOT / "scripts/generate-env.sh").read_text()
    example = (ROOT / ".env.example").read_text()
    assert '[[ "$EUID" -eq 0 ]]' in generator
    assert "TELNEXA_RUNTIME_SECRET_DIR:-/etc/telnexa/secrets" in generator
    assert "provider-keys.json" in generator
    assert "install -o root -g root -m 0600" in generator
    for name in ("TELNEXA_PROVIDER_KEYS_FILE", "TELNEXA_METRICS_TOKEN_FILE", "OIDC_ALLOWED_AZP"):
        assert name in generator and name in example


def test_all_documented_jasmin_callbacks_are_authenticated():
    guide = (ROOT / "docs/ADDING_SMPP_PROVIDER.md").read_text()
    example = (ROOT / "examples/smpp-provider.env.example").read_text()
    for content in (guide, example):
        assert "/events/inbound?source_key_id=" in content
        assert "/events/dlr?source_key_id=" in content
        assert "source_token=" in content


def test_receiver_documentation_matches_hmac_v1_contract():
    readme = (ROOT / "README.md").read_text()
    for value in (
        "X-Signature-Version: v1",
        "uppercase HTTP method",
        "normalized path",
        "event ID",
        "SHA-256 of the exact request body",
    ):
        assert value in readme
    assert 'timestamp + "." + raw_request_body' not in readme


def test_legacy_idempotency_hashes_are_explicitly_compatible():
    migration = (ROOT / "billing/migrations/003_message_request_hash.sql").read_text()
    source = (ROOT / "billing/app.py").read_text()
    assert "'legacy:' || md5" in migration
    assert "legacy_hash = (" in source
    assert '"legacy:"' in source
    assert "prior.request_hash not in (request_hash, legacy_hash)" in source
