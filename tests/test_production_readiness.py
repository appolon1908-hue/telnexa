from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_portal_and_api_use_the_local_telnexa_identity_authority():
    source = (ROOT / "billing/app.py").read_text()
    oidc = (ROOT / "billing/oidc.py").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    assert "connect-src 'self';" in source
    assert "const issuer='/auth/realms/telnexa'" in source
    assert "https://auth.codestra.co" not in source
    assert "https://api.telnexa.co/auth/realms/telnexa" in oidc
    assert "http://keycloak:8080/auth/realms/telnexa/protocol/openid-connect/certs" in oidc
    assert "keycloak-configure: {condition: service_completed_successfully}" in compose


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


def test_quick_start_binds_source_and_requires_immutable_release_images():
    generator = (ROOT / "scripts/generate-env.sh").read_text()
    example = (ROOT / ".env.example").read_text()
    start = (ROOT / "scripts/start.sh").read_text()
    release_images = (
        "TELNEXA_KEYCLOAK_IMAGE",
        "TELNEXA_JASMIN_IMAGE",
        "TELNEXA_WEBHOOK_RELAY_IMAGE",
        "TELNEXA_NGINX_IMAGE",
        "TELNEXA_BILLING_IMAGE",
        "TELNEXA_GRAFANA_IMAGE",
    )

    assert "SOURCE_SHA=" in example
    assert 'set_value SOURCE_SHA "$source_sha"' in generator
    assert "diff --quiet HEAD --" in generator
    assert all(f"{name}=example.invalid/" in example for name in release_images)
    assert "Refusing placeholder images" in start
    assert 'configured_source=$(sed -n' in start
    assert '"${COMPOSE[@]}" up -d --no-build' in start
    assert '"${COMPOSE[@]}" up -d --build' not in start


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


def test_internal_provider_inbox_and_raw_jasmin_are_not_public():
    nginx = (ROOT / "docker/nginx/tls.conf.template").read_text()
    assert "location ^~ /internal/" in nginx
    assert "return 410" in nginx
    assert "proxy_pass http://jasmin:1401" not in nginx


def test_jasmin_secret_names_are_one_explicit_contract():
    compose = (ROOT / "docker-compose.yml").read_text()
    worker = (ROOT / "billing/dispatch_worker.py").read_text()
    generator = (ROOT / "scripts/generate-env.sh").read_text()
    for name in ("jasmin_http_username", "jasmin_http_password", "jasmin_http_dlr_token"):
        assert name in compose
    assert "provider.credential_reference" in worker
    assert 'or "/run/secrets/jasmin"' not in worker
    for name in ("jasmin-http-username", "jasmin-http-password", "jasmin-dlr-token"):
        assert name in generator
