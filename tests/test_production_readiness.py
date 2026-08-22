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


def test_legacy_idempotency_hashes_are_explicitly_compatible():
    migration = (ROOT / "billing/migrations/003_message_request_hash.sql").read_text()
    source = (ROOT / "billing/app.py").read_text()
    assert "'legacy:' || md5" in migration
    assert "legacy_hash = (" in source
    assert '"legacy:"' in source
    assert "prior.request_hash not in (request_hash, legacy_hash)" in source
