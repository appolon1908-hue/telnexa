"""Source-only cross-server contract regressions; never contact a runtime."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def contract():
    path = ROOT / "config/telnexa-codestra-cross-server-sms-contract.yaml"
    return yaml.safe_load(path.read_text())


def test_cross_server_contract_preserves_appolon_and_separate_identity_authority():
    data = contract()
    assert data["repositories"]["middleware"] == "appolon1908-hue/Middleware-"
    assert all(value.startswith("appolon1908-hue/") for value in data["repositories"].values())
    identity = data["canonical_identity"]
    assert identity["audience"] == "telnexa-gateway"
    assert identity["purpose"] == "machine_to_machine_only"
    assert identity["replaces_local_portal_authority"] is False
    assert identity["local_portal_audience"] == "telnexa-api"
    assert identity["default_enabled"] is False
    for api in ("middleware_api", "telnexa_api"):
        assert data[api]["send"]["scope"] == "sms.send"
        for name in ("message", "events"):
            assert data[api][name]["scope"] == "sms.status.read"


def test_cross_server_callbacks_cannot_skip_local_durable_authority():
    data = contract()
    flow = data["architecture"]["inbound_event"]
    assert flow.index("telnexa_provider_event_inbox") < flow.index("middleware_event_receiver")
    assert flow.index("telnexa_event_processor") < flow.index("telnexa_integration_outbox")
    inbox = data["telnexa_api"]["provider_event_inbox"]
    assert inbox["path"] == "/internal/v1/provider-events/jasmin"
    assert inbox["authenticated_source_required"] is True
    assert inbox["signed_raw_body_required"] is True
    assert inbox["provider_callback_may_select_tenant"] is False
    assert data["retry_rules"]["ambiguous_submission_resubmit"] is False
    assert data["retry_rules"]["middleware_may_fallback_to_jasmin"] is False


def test_planning_source_cannot_claim_runtime_certification_or_enable_delivery():
    data = contract()
    assert data["status"] == "planning_authority"
    assert data["source_reconciliation"]["runtime_verified"] is False
    assert data["source_reconciliation"]["source_merge_authorizes_deployment"] is False
    flags = data["feature_flags"]["required_fail_closed_before_real_canary"]
    assert "LIVE_SMS_DELIVERY" in flags
    assert "TELNEXA_PRODUCTION_SMS_ENABLED" in flags
    assert all(value is False for value in flags.values())
    assert data["real_canary"]["default_enabled"] is False
    assert data["real_canary"]["durable_max_deliveries"] == 1
