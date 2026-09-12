import json
from pathlib import Path

import yaml

from billing.app import app
from billing.observability import SIGNAL_OWNERS, observability_contract

ROOT = Path(__file__).resolve().parents[1]


def test_observability_contract_is_versioned_and_redacted(monkeypatch):
    monkeypatch.setenv("SOURCE_SHA", "a" * 40)
    monkeypatch.setenv("TELNEXA_BILLING_IMAGE_DIGEST", "sha256:" + "b" * 64)
    monkeypatch.setenv("TELNEXA_CONFIG_DIGEST", "sha256:" + "c" * 64)

    contract = observability_contract("v1")

    assert contract["contract_version"] == "telnexa.observability.v1"
    assert contract["service"]["service_id"] == "telnexa-billing-api"
    assert contract["deployment"]["git_sha"] == "a" * 40
    assert contract["deployment"]["image_digest"].startswith("sha256:")
    assert contract["security"]["direct_odoo_credentials"] is False
    assert "middleware_api_key" not in json.dumps(contract)
    assert "middleware_hmac_secret" not in json.dumps(contract)


def test_v2_contract_and_routes_are_present():
    contract = observability_contract("v2")
    assert contract["contract_version"] == "telnexa.observability.v2"

    paths = {route.path for route in app.routes}
    assert "/api/v1/integration/observability" in paths
    assert "/api/v2/integration/observability" in paths


def test_signal_owners_match_the_cross_repository_inventory():
    expected = {
        "metrics": "Codestra-Prometheus",
        "alerts": "Codestra-Alertmanager",
        "dashboards": "Codestra-Grafana",
        "telemetry": "Codestra-Telemetry",
        "collector": "Codestra-Alloy",
        "logs": "Codestra-Loki",
        "traces": "Codestra-Tempo",
        "host_metrics": "Codestra-Node-Exporter",
        "container_metrics": "Codestra-cAdvisor",
        "redis_metrics": "Codestra-Redis-Exporter",
        "probe_metrics": "Codestra-Blackbox-Exporter",
        "postgres_metrics": "Codestra-Postgres-Exporter",
        "business_analytics": "Superset",
        "secrets": "Codestra-OpenBao",
    }
    assert SIGNAL_OWNERS == expected


def test_openapi_contract_covers_both_versions():
    spec = yaml.safe_load(
        (ROOT / "contracts/observability/telnexa-monitoring.openapi.yaml").read_text()
    )
    assert spec["openapi"] == "3.1.0"
    assert set(spec["paths"]) == {
        "/api/v1/integration/observability",
        "/api/v2/integration/observability",
    }
    assert spec["components"]["schemas"]["ServiceIdentity"]["properties"]["service_id"][
        "const"
    ] == "telnexa-billing-api"


def test_manifest_registers_the_runtime_contract_without_enabling_activation():
    manifest = json.loads((ROOT / "monitoring-integration.v1.json").read_text())
    assert "telnexa-billing-api" in manifest["service_ids"]
    assert manifest["activation_enabled"] is False
    assert manifest["runtime_coverage"] == "contract_registered_unverified"
    assert manifest["odoo_boundary"]["direct_write"] is False
