import datetime
from collections import Counter
from pathlib import Path

from openapi_spec_validator import validate

if not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc

from billing.app import app


def test_required_canonical_telnexa_routes_are_documented():
    paths = app.openapi()["paths"]
    required = {
        ("get", "/v1/me"),
        ("get", "/v1/accounts"),
        ("get", "/v1/accounts/{account_id}"),
        ("get", "/v1/tenants"),
        ("get", "/v1/tenants/{requested_tenant_id}"),
        ("get", "/v1/service-accounts"),
        ("post", "/v1/service-accounts"),
        ("get", "/v1/api-keys"),
        ("post", "/v1/api-keys"),
        ("delete", "/v1/api-keys/{key_id}"),
        ("post", "/v1/sms/messages"),
        ("get", "/v1/sms/messages"),
        ("get", "/v1/sms/messages/{message_id}"),
        ("post", "/v1/sms/messages/{message_id}/cancel"),
        ("get", "/v1/sms/delivery-reports"),
        ("get", "/v1/sms/providers"),
        ("get", "/v1/sms/providers/{provider_id}/health"),
        ("get", "/v1/smpp/accounts"),
        ("post", "/v1/smpp/accounts"),
        ("get", "/v1/smpp/accounts/{account_id}"),
        ("patch", "/v1/smpp/accounts/{account_id}"),
        ("get", "/v1/messaging/messages"),
        ("get", "/v1/dids"),
        ("get", "/v1/dids/{did_id}"),
        ("get", "/v1/routing/routes"),
        ("get", "/v1/billing/account"),
        ("get", "/v1/billing/usage"),
        ("get", "/v1/billing/invoices"),
        ("get", "/v1/usage"),
        ("post", "/v1/webhooks/sms/delivery"),
        ("post", "/v1/webhooks/provider/{provider}"),
        ("get", "/v1/delivery-reports"),
        ("get", "/v1/operations"),
        ("get", "/v1/operations/{operation_id}"),
        ("get", "/v1/operations/{operation_id}/events"),
        ("get", "/v1/operations/{operation_id}/attempts"),
        ("post", "/v1/operations/{operation_id}/cancel"),
        ("post", "/v1/operations/{operation_id}/reconcile"),
        ("get", "/v1/audit"),
        ("get", "/v1/admin/health"),
    }
    required = {(method, f"/api{path}") for method, path in required}
    missing = sorted(
        f"{method.upper()} {path}" for method, path in required if method not in paths.get(path, {})
    )
    assert missing == []
    assert not [path for path in paths if path == "/v1" or path.startswith("/v1/")]


def test_canonical_openapi_is_structurally_valid():
    validate(app.openapi())


def test_runtime_has_one_handler_per_method_and_path():
    operations = []
    for outer_route in app.routes:
        included = getattr(getattr(outer_route, "original_router", None), "routes", None)
        for route in included or [outer_route]:
            operations.extend(
                (method, route.path)
                for method in getattr(route, "methods", set())
                if method not in {"HEAD", "OPTIONS"}
            )
    duplicates = sorted(operation for operation, count in Counter(operations).items() if count > 1)
    assert duplicates == []


def test_schema_is_not_created_during_api_import():
    assert "Base.metadata.create_all(engine)" not in Path("billing/app.py").read_text()
    assert (
        "CREATE TABLE IF NOT EXISTS service_accounts"
        in Path("billing/migrations/006_full_platform_api.sql").read_text()
    )
    command_migration = Path("billing/migrations/007_command_idempotency.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS command_idempotency" in command_migration
    assert "uq_command_idempotency_identity" in command_migration
    smpp_gate = Path("billing/migrations/008_smpp_runtime_enablement_gate.sql").read_text()
    assert "ck_smpp_credentials_runtime_provisioned_before_enable" in smpp_gate
    assert "SET enabled = false" in smpp_gate
    validator = Path("scripts/validate_postgres_migrations.py").read_text()
    assert "SMPP runtime enablement gate missing" in validator


def test_smpp_command_headers_are_required_by_openapi():
    paths = app.openapi()["paths"]
    for method, path in (
        ("post", "/api/v1/smpp/accounts"),
        ("patch", "/api/v1/smpp/accounts/{account_id}"),
    ):
        headers = {
            item["name"]: item
            for item in paths[path][method]["parameters"]
            if item["in"] == "header"
        }
        assert headers["idempotency-key"]["required"] is True
        assert headers["x-correlation-id"]["required"] is True
        status = "201" if method == "post" else "200"
        response_headers = paths[path][method]["responses"][status]["headers"]
        assert "Idempotency-Replayed" in response_headers


def test_keycloak_realm_requires_mfa_and_security_audit():
    import json

    realm = json.loads(Path("config/keycloak/telnexa-realm.json").read_text())
    actions = {item["alias"]: item for item in realm["requiredActions"]}
    assert actions["CONFIGURE_TOTP"]["enabled"] is True
    assert actions["CONFIGURE_TOTP"]["defaultAction"] is True
    assert realm["verifyEmail"] is True
    assert realm["bruteForceProtected"] is True
    assert realm["eventsEnabled"] is True
    assert realm["adminEventsEnabled"] is True
    assert realm["adminEventsDetailsEnabled"] is True
