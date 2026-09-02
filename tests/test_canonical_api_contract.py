import datetime
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
    missing = sorted(
        f"{method.upper()} {path}" for method, path in required if method not in paths.get(path, {})
    )
    assert missing == []


def test_canonical_openapi_is_structurally_valid():
    validate(app.openapi())


def test_schema_is_not_created_during_api_import():
    assert "Base.metadata.create_all(engine)" not in Path("billing/app.py").read_text()
    assert (
        "CREATE TABLE IF NOT EXISTS service_accounts"
        in Path("billing/migrations/006_full_platform_api.sql").read_text()
    )


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
