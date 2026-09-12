"""Versioned, read-only service contract for the Telnexa observability plane.

Telnexa exposes service metadata and its private Prometheus scrape endpoint. It
does not receive monitoring writes and it never writes Odoo. Middleware owns
cross-system observability ingestion and the only Odoo write path.
"""

from __future__ import annotations

import os
from typing import Any

REPOSITORY = "appolon1908-hue/telnexa"
SERVICE_ID = "telnexa-billing-api"

SIGNAL_OWNERS: dict[str, str] = {
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


def _env(*names: str, default: str = "unverified") -> str:
    """Read release identity metadata without exposing credentials."""

    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def _signal_owners() -> dict[str, str]:
    return dict(SIGNAL_OWNERS)


def observability_contract(version: str = "v1") -> dict[str, Any]:
    """Return the service contract consumed by Middleware service discovery.

    The response is intentionally descriptive and read-only. It contains paths,
    ownership and release identity, never tokens, certificates, private URLs or
    customer data.
    """

    if version not in {"v1", "v2"}:
        raise ValueError("unsupported_observability_contract_version")

    return {
        "schema_version": 1,
        "contract_version": f"telnexa.observability.{version}",
        "service": {
            "service_id": SERVICE_ID,
            "name": "Telnexa SMS Gateway",
            "suite": "Kyyow Communications",
            "repository": REPOSITORY,
            "domain": "sms",
            "tenant_mode": "multi-tenant",
        },
        "deployment": {
            "environment": _env("DEPLOYMENT_ENVIRONMENT", "ENVIRONMENT"),
            "source_deployment": _env("SOURCE_DEPLOYMENT"),
            "git_sha": _env("SOURCE_SHA"),
            "image_digest": _env("TELNEXA_BILLING_IMAGE_DIGEST", "IMAGE_DIGEST"),
            "config_digest": _env("TELNEXA_CONFIG_DIGEST", "CONFIG_DIGEST"),
        },
        "endpoints": {
            "health": "/healthz",
            "readiness": "/readyz",
            "metrics": "/metrics",
            "service_contract_v1": "/api/v1/integration/observability",
            "service_contract_v2": "/api/v2/integration/observability",
            "openapi": "/api/v1/openapi.json",
        },
        "signals": {
            "metrics": {
                "owner": SIGNAL_OWNERS["metrics"],
                "mode": "private-prometheus-scrape",
                "path": "/metrics",
            },
            "alerts": {
                "owner": SIGNAL_OWNERS["alerts"],
                "mode": "native-alertmanager-webhook",
                "middleware_path": "/v1/integrations/alertmanager/events",
                "status_path": "/v1/integrations/alertmanager/status-events",
            },
            "logs": {
                "owner": SIGNAL_OWNERS["logs"],
                "mode": "collector-forwarded",
                "direct_telnexa_write": False,
            },
            "traces": {
                "owner": SIGNAL_OWNERS["traces"],
                "mode": "collector-forwarded",
                "direct_telnexa_write": False,
            },
            "analytics": {
                "owner": SIGNAL_OWNERS["business_analytics"],
                "mode": "read-only",
                "direct_telnexa_write": False,
            },
        },
        "signal_owners": _signal_owners(),
        "integration": {
            "middleware": {
                "role": "cross-system operational and integration write boundary",
                "event_path": "/api/v1/events/telnexa",
                "runtime_observations_path": "/platform/v1/runtime/observations",
                "heartbeats_path": "/platform/v1/telemetry/heartbeats",
                "contract_refresh_path": "/platform/v1/services/{service_id}/contract-refresh",
                "coverage_path": "/platform/v1/services/{service_id}/coverage",
                "authentication": "tenant-bound OIDC or service identity; mTLS/HMAC for Telnexa outbox",
            },
            "odoo": {
                "direct_write": False,
                "writer": "Middleware",
                "transport": "Middleware-owned Odoo JSON-2 adapter",
                "accepted_data": [
                    "approved KPI snapshots",
                    "operational incidents after Middleware policy",
                    "sync health and reconciliation state",
                ],
            },
        },
        "security": {
            "metadata_access": "authenticated service read",
            "metrics_access": "private network bearer token",
            "secrets_owner": SIGNAL_OWNERS["secrets"],
            "customer_data_in_contract": False,
            "direct_odoo_credentials": False,
        },
    }
