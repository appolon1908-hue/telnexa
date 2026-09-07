"""Validate the source-only SMS fabric contract without invoking any runtime."""

import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CAPABILITIES = {
    "SMS_DELIVERY",
    "SMS_CAMPAIGN_SEND",
    "SMS_SENDER_WRITE",
    "ODOO_WRITE",
    "DEAD_LETTER_REPLAY",
}
CALLBACK = "/internal/v1/provider-events/jasmin"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(root=ROOT):
    directory = Path(root) / "docs/integrations/codestra-fabric"
    manifest = json.loads((directory / "manifest.v2.json").read_text())
    require(manifest.get("schema_version") == "2.0", "unsupported manifest version")
    require(manifest.get("service_identity") == "telnexa-gateway", "wrong machine identity")
    require(manifest.get("integration_boundary") == "MIDDLEWARE_ONLY", "boundary bypass")
    for name in ("n8n_direct_access", "jasmin_direct_access", "smpp_credentials_in_n8n"):
        require(manifest.get(name) is False, f"unsafe or missing {name}")
    require(
        manifest.get("event_delivery") == "DURABLE_INBOX_AND_TRANSACTIONAL_OUTBOX",
        "durable event authority required",
    )
    require(manifest.get("unknown_submission_reconcile_before_retry") is True, "readback required")
    require(manifest.get("unknown_submission_blind_resubmit") is False, "blind resubmit denied")
    flags = manifest.get("capabilities", {})
    require(set(flags) == CAPABILITIES, "missing or unexpected capability")
    require(all(value is False for value in flags.values()), "live capability enabled")
    spec = yaml.safe_load((directory / "sms-api.openapi.yaml").read_text())
    require(spec.get("openapi") == "3.0.3", "unsupported OpenAPI version")
    require(spec.get("x-contract-status") == "DESIGN_ONLY_NOT_RUNTIME", "source status required")
    schemes = spec["components"]["securitySchemes"]
    oauth = schemes["codestraOAuth"]["flows"]["clientCredentials"]
    require(
        oauth["tokenUrl"] == "https://auth.codestra.co/realms/codestra/protocol/openid-connect/token",
        "wrong token authority",
    )
    require("sms.send" in oauth["scopes"], "send scope missing")
    require("sms.status.read" in oauth["scopes"], "readback scope missing")
    require("sms.message.send" not in oauth["scopes"], "obsolete send scope")
    require("sms.message.read" not in oauth["scopes"], "obsolete readback scope")
    for path, item in spec["paths"].items():
        for method, operation in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            require(bool(operation.get("security")), f"anonymous operation: {path}")
            if path == CALLBACK:
                require(operation["security"] == [{"providerSignature": []}], "signed ingress required")
                require(operation.get("x-private-ingress") is True, "private ingress required")
            else:
                require(operation["security"] == [{"codestraOAuth": operation["x-required-scopes"]}], "scope mismatch")
                require(
                    {"$ref": "#/components/parameters/TenantId"} in item.get("parameters", []),
                    "tenant binding missing",
                )
    require(CALLBACK in spec["paths"], "durable provider inbox missing")
    return manifest


if __name__ == "__main__":
    validate()
    print("SOURCE_SMS_FABRIC_VALID=YES; RUNTIME_CERTIFIED=NO; LIVE_DELIVERY=DISABLED")
