from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_portal_and_api_use_the_local_telnexa_identity_authority():
    source = (ROOT / "billing/app.py").read_text()
    oidc = (ROOT / "billing/oidc.py").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    nginx = (ROOT / "docker/nginx/tls.conf.template").read_text()
    realm = (ROOT / "config/keycloak/telnexa-realm.json").read_text()
    configure = (ROOT / "scripts/keycloak-configure.sh").read_text()
    assert "connect-src 'self';" in source
    assert "const issuer='/auth/realms/telnexa'" in source
    assert "https://auth.codestra.co" not in source
    assert "https://api.telnexa.co/auth/realms/telnexa" in oidc
    assert "http://keycloak:8080/auth/realms/telnexa/protocol/openid-connect/certs" in oidc
    assert "keycloak-configure: {condition: service_completed_successfully}" in compose
    assert nginx.count("location ^~ /auth/") == 2
    assert "proxy_pass http://keycloak:8080" in nginx
    assert "server_name ${PORTAL_DOMAIN};" in nginx
    assert "server_name ${ADMIN_DOMAIN};" in nginx
    assert "server_name ${STATUS_DOMAIN};" in nginx
    assert "server_name ${ADMIN_DOMAIN};" in nginx and "return 403;" in nginx
    assert "https://app.telnexa.co/*" in realm
    assert "https://admin.telnexa.co/*" not in realm
    assert "clientId=telnexa-portal" in configure
    assert 'update "clients/$portal_client_id"' in configure
    assert "--fields redirectUris,webOrigins" in configure
    assert "get authentication/required-actions -r telnexa" in configure
    assert "if grep -qx 'VERIFY_PROFILE'" in configure
    assert "Expected exactly one Telnexa portal client identity" in configure
    assert "sed -n '2p'" not in configure


def test_tls_certificate_covers_every_configured_public_hostname():
    example = (ROOT / ".env.example").read_text()
    tls_init = (ROOT / "scripts/tls-init.sh").read_text()
    entrypoint = (ROOT / "docker/nginx/entrypoint.sh").read_text()

    for name in ("SMS_DOMAIN", "API_DOMAIN", "PORTAL_DOMAIN", "ADMIN_DOMAIN", "STATUS_DOMAIN"):
        assert f"{name}=" in example
        assert f'"${name}"' in tls_init
        assert f"${{{name}}}" in entrypoint


def test_nginx_runs_unprivileged_on_high_container_ports():
    dockerfile = (ROOT / "docker/nginx/Dockerfile").read_text()
    compose = (ROOT / "docker-compose.yml").read_text()
    http = (ROOT / "docker/nginx/http.conf.template").read_text()
    tls = (ROOT / "docker/nginx/tls.conf.template").read_text()

    assert "nginxinc/nginx-unprivileged:1.30.4-alpine@sha256:" in dockerfile
    assert "USER 101:101" in dockerfile
    assert "listen 8080 default_server" in http
    assert "listen 8080 default_server" in tls
    assert tls.count("listen 8443 ssl") == 5
    assert "nginx-cert-permissions:" in compose
    assert "network_mode: none" in compose
    assert "80}:8080" in compose
    assert "443}:8443" in compose
    assert "read_only: true" in compose
    assert "cap_drop: [ALL]" in compose


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
    assert "@sha256:[0-9a-f]{64}" in start
    assert "example.invalid/*" in start
    assert "@sha256:0{64}" in start
    assert "Refusing mutable or invalid image reference" in start
    assert "configured_source=$(sed -n" in start
    assert "git status --porcelain --untracked-files=normal" in start
    assert "org.opencontainers.image.revision" in start
    assert "Refusing source/image drift" in start
    assert '"${COMPOSE[@]}" up -d --no-build' in start
    assert '"${COMPOSE[@]}" up -d --build' not in start


def test_jasmin_upgrade_has_bounded_volume_and_topology_migrations():
    compose = (ROOT / "docker-compose.yml").read_text()
    dockerfile = (ROOT / "docker/jasmin/Dockerfile").read_text()
    start = (ROOT / "scripts/start.sh").read_text()
    rabbit_plugins = (ROOT / "config/rabbitmq/enabled_plugins").read_text()

    assert "adduser -u 10001" in dockerfile
    assert "jasmin-volume-migrate:" in compose
    assert "chown -R 10001:10001" in compose
    assert "jasmin-topology-migrate:" in compose
    assert "service_completed_successfully" in compose
    assert "JASMIN_DURABILITY_MIGRATION_AUTHORIZED" in compose
    assert rabbit_plugins.strip() == "[rabbitmq_management]."
    assert '"$REPO_DIR/scripts/backup.sh"' in start
    assert "MIGRATION_MODE=check" in start
    assert "stop jasmin" in start
    assert "MIGRATION_MODE=apply" in start
    assert "recover_stopped_jasmin" in start
    assert 'docker start "$existing_jasmin"' in start


def test_release_and_merge_result_provenance_dependencies_are_exact():
    requirements = (ROOT / "billing/requirements.txt").read_text()
    ci = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "openapi-spec-validator==0.7.2" in requirements
    assert "merge-result-quality-and-tests:" in ci
    assert "SOURCE_SHA: ${{ github.sha }}" in ci


def test_backup_restore_verifies_integrity_and_never_builds_on_server():
    backup = (ROOT / "scripts/backup.sh").read_text()
    restore = (ROOT / "scripts/restore.sh").read_text()
    generator = (ROOT / "scripts/generate-env.sh").read_text()
    update = (ROOT / "scripts/update.sh").read_text()

    assert 'sha256sum "${artifacts[@]}" > SHA256SUMS' in backup
    assert "sha256sum --check SHA256SUMS" in backup
    assert "TELNEXA_BACKUP_RECIPIENT_FILE" in backup
    assert "TELNEXA_BACKUP_STAGING_ROOT" in backup
    assert "TELNEXA_OFF_HOST_BACKUP_DIR" in backup
    assert 'stat -f -c %T "$staging_root"' in backup
    assert "mountpoint -q" in backup and "findmnt -n -o SOURCE" in backup
    assert "--encrypt" in backup and "tar.gz.gpg" in backup
    assert 'sha256sum --check "$(basename "$checksum")"' in backup
    assert "rabbitmqctl --quiet export_definitions" in backup
    assert 'keycloak.pgdump"' in backup
    assert ".secrets config docker" in backup
    assert 'runtime-secrets.tar.gz"' in backup
    assert 'runtime-mtls.tar.gz"' in backup
    assert 'python3 -m json.tool "$stage/rabbitmq-definitions.json"' in backup
    assert 'test -f "$backup/SHA256SUMS"' in restore
    assert "sha256sum --check SHA256SUMS" in restore
    assert "TELNEXA_BACKUP_PRIVATE_KEY_FILE" in restore
    assert "CONFIRM_RESTORE=RESTORE_TELNEXA" in restore
    assert "--decrypt" in restore
    assert 'stat -f -c %T "$staging_root"' in restore
    assert 'tar -xzf "$backup/repository-config.tar.gz"' not in restore
    assert "jasmin-config.tar.gz redis-data.tar.gz" in restore
    assert 'test -f "$archive" || continue' not in restore
    assert 'tar -xzf "$backup/runtime-secrets.tar.gz"' in restore
    assert 'tar -xzf "$backup/runtime-mtls.tar.gz"' in restore
    assert "billing-db keycloak-db redis rabbitmq" in restore
    assert "grep -Fx 'rabbitmq-data'" in restore
    assert 'docker volume rm "$rabbitmq_volume_name"' in restore
    assert "rabbitmqctl --quiet import_definitions" in restore
    assert (
        restore.index('docker volume rm "$rabbitmq_volume_name"')
        < restore.index('"${COMPOSE[@]}" up -d --no-build')
        < restore.index("rabbitmqctl --quiet import_definitions")
    )
    assert '"$REPO_DIR/scripts/start.sh"' in restore
    assert "up -d --build" not in restore
    assert "keycloak_admin_password keycloak_db_password" in generator
    assert '"$REPO_DIR/scripts/generate-env.sh"' in update
    assert '"$REPO_DIR/scripts/start.sh"' in update
    assert "--build" not in update


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
